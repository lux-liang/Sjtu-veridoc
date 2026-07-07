#!/usr/bin/env python3
import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import torch
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
from torchvision import models, transforms


class PageDataset(Dataset):
    def __init__(self, rows, class_to_idx, image_size, train):
        self.rows = rows
        self.class_to_idx = class_to_idx
        aug = [
            transforms.Resize((image_size, image_size)),
        ]
        if train:
            aug.extend(
                [
                    transforms.RandomApply([transforms.ColorJitter(0.1, 0.1, 0.1, 0.02)], p=0.3),
                    transforms.RandomRotation(1.5),
                ]
            )
        aug.extend(
            [
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ]
        )
        self.transform = transforms.Compose(aug)

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        row = self.rows[index]
        image = Image.open(row["image_path"]).convert("RGB")
        return self.transform(image), self.class_to_idx[row["doc_type"]]


def split_by_document(rows, val_ratio, seed):
    docs = {}
    for row in rows:
        docs.setdefault(row["document_id"], []).append(row)
    docs_by_type = defaultdict(list)
    for doc_id, doc_rows in docs.items():
        docs_by_type[doc_rows[0]["doc_type"]].append(doc_id)

    rng = random.Random(seed)
    val_ids = set()
    for doc_type, doc_ids in docs_by_type.items():
        rng.shuffle(doc_ids)
        if len(doc_ids) < 2:
            continue
        val_count = max(1, int(round(len(doc_ids) * val_ratio)))
        val_count = min(val_count, len(doc_ids) - 1)
        val_ids.update(doc_ids[:val_count])

    train_rows, val_rows = [], []
    for doc_id, doc_rows in docs.items():
        (val_rows if doc_id in val_ids else train_rows).extend(doc_rows)
    return train_rows, val_rows


def summarize_rows(rows):
    by_page = Counter(row["doc_type"] for row in rows)
    by_doc = {}
    grouped = defaultdict(set)
    for row in rows:
        grouped[row["doc_type"]].add(row["document_id"])
    for doc_type, doc_ids in grouped.items():
        by_doc[doc_type] = len(doc_ids)
    return {"pages": dict(sorted(by_page.items())), "documents": dict(sorted(by_doc.items()))}


def class_weights(rows, class_to_idx):
    counts = Counter(row["doc_type"] for row in rows)
    total = sum(counts.values())
    weights = [0.0] * len(class_to_idx)
    for name, idx in class_to_idx.items():
        weights[idx] = total / max(counts[name], 1)
    mean = sum(weights) / max(len(weights), 1)
    return torch.tensor([value / mean for value in weights], dtype=torch.float32)


def weighted_sampler(rows, sampler_alpha):
    counts = Counter(row["doc_type"] for row in rows)
    sample_weights = [1.0 / (max(counts[row["doc_type"]], 1) ** sampler_alpha) for row in rows]
    return WeightedRandomSampler(sample_weights, num_samples=len(rows), replacement=True)


def evaluate(model, loader, device, criterion, classes):
    model.eval()
    correct = 0
    total = 0
    loss_sum = 0.0
    confusion = [[0 for _ in classes] for _ in classes]
    with torch.no_grad():
        for images, labels in loader:
            images = images.to(device)
            labels = labels.to(device)
            logits = model(images)
            loss = criterion(logits, labels)
            preds = logits.argmax(1)
            loss_sum += float(loss.item()) * labels.size(0)
            correct += int((preds == labels).sum().item())
            total += labels.size(0)
            for label, pred in zip(labels.cpu().tolist(), preds.cpu().tolist()):
                confusion[label][pred] += 1

    per_class = {}
    f1_values = []
    recall_values = []
    for idx, name in enumerate(classes):
        tp = confusion[idx][idx]
        support = sum(confusion[idx])
        predicted = sum(row[idx] for row in confusion)
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if support:
            f1_values.append(f1)
            recall_values.append(recall)
        per_class[name] = {
            "support": support,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    return {
        "loss": loss_sum / max(total, 1),
        "acc": correct / max(total, 1),
        "macro_recall": sum(recall_values) / max(len(recall_values), 1),
        "macro_f1": sum(f1_values) / max(len(f1_values), 1),
        "per_class": per_class,
        "confusion": confusion,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--render-manifest", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--image-size", type=int, default=224)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--num-workers", type=int, default=8)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--weighted-sampler", action="store_true")
    parser.add_argument("--class-weighted-loss", action="store_true")
    parser.add_argument("--sampler-alpha", type=float, default=1.0)
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    random.seed(args.seed)

    with Path(args.render_manifest).open("r", encoding="utf-8") as f:
        rows = [row for row in csv.DictReader(f) if row["doc_type"] != "other"]
    classes = sorted({row["doc_type"] for row in rows})
    class_to_idx = {name: idx for idx, name in enumerate(classes)}
    train_rows, val_rows = split_by_document(rows, args.val_ratio, args.seed)
    split_summary = {
        "classes": classes,
        "train": summarize_rows(train_rows),
        "val": summarize_rows(val_rows),
    }

    sampler = weighted_sampler(train_rows, args.sampler_alpha) if args.weighted_sampler else None
    train_loader = DataLoader(
        PageDataset(train_rows, class_to_idx, args.image_size, True),
        batch_size=args.batch_size,
        shuffle=sampler is None,
        sampler=sampler,
        num_workers=args.num_workers,
        pin_memory=True,
    )
    val_loader = DataLoader(
        PageDataset(val_rows, class_to_idx, args.image_size, False),
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=args.num_workers,
        pin_memory=True,
    )

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = models.resnet18(weights=None)
    model.fc = nn.Linear(model.fc.in_features, len(classes))
    model = model.to(device)
    if torch.cuda.device_count() > 1:
        model = nn.DataParallel(model)

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    weights = class_weights(train_rows, class_to_idx).to(device) if args.class_weighted_loss else None
    criterion = nn.CrossEntropyLoss(weight=weights)
    scaler = torch.amp.GradScaler("cuda", enabled=torch.cuda.is_available())
    best_macro_f1 = -1.0
    history = []
    model_dir = Path(args.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "split_summary.json").write_text(json.dumps(split_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    for epoch in range(1, args.epochs + 1):
        model.train()
        running = 0.0
        seen = 0
        for images, labels in train_loader:
            images = images.to(device)
            labels = labels.to(device)
            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", enabled=torch.cuda.is_available()):
                logits = model(images)
                loss = criterion(logits, labels)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            running += float(loss.item()) * labels.size(0)
            seen += labels.size(0)
        metrics = evaluate(model, val_loader, device, criterion, classes)
        record = {"epoch": epoch, "train_loss": running / max(seen, 1), **metrics}
        history.append(record)
        compact = {key: value for key, value in record.items() if key not in {"per_class", "confusion"}}
        print(json.dumps(compact, ensure_ascii=False), flush=True)
        if metrics["macro_f1"] > best_macro_f1:
            best_macro_f1 = metrics["macro_f1"]
            state = model.module.state_dict() if isinstance(model, nn.DataParallel) else model.state_dict()
            torch.save({"state_dict": state, "classes": classes, "metrics": metrics}, model_dir / "doc_classifier_best.pt")

    (model_dir / "doc_classifier_history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    (model_dir / "classes.json").write_text(json.dumps(classes, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
