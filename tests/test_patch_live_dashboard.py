import tempfile
import unittest
from pathlib import Path

from scripts.patch_live_dashboard import (
    BUSINESS_MARKER,
    CSS_MARKER,
    EVAL_MARKER,
    JS_MARKER,
    OCR_REASON_JS_MARKER,
    SEAL_DETAIL_JS_MARKER,
    SEAL_REASON_JS_MARKER,
    find_web_files,
    patch_css,
    patch_index,
    patch_javascript,
)


INDEX_FIXTURE = '''<!doctype html>
<html><body>
<h1>明鉴 · 材料真伪智能核验平台</h1>
<section class="view active" id="view-overview">
        <section class="metrics" id="metrics"></section>
</section>
<section class="view" id="view-principles">
<h2>五类风险检测原理</h2>
        <article class="panel pr-card c-blue">PDF</article>
</section>
</body></html>
'''

JS_FIXTURE = '''const state = {};
const REASON_META = {
  "visual:red_stamp_like_region": ["seal", "红章区域"],
};
const RISK_BANDS = [];
function openDetail(row) {
  const fields = [
    ["字体警告", row.font_warning_count],
  ];
}
async function reloadDashboard() {
  renderRiskDist(state.dashboard);
}
'''


class DashboardPatchTests(unittest.TestCase):
    def test_index_patch_is_idempotent(self):
        once = patch_index(INDEX_FIXTURE)
        twice = patch_index(once)
        self.assertEqual(once, twice)
        self.assertIn(EVAL_MARKER, once)
        self.assertIn(BUSINESS_MARKER, once)
        self.assertIn("技术与业务双视角", once)

    def test_javascript_patch_is_idempotent(self):
        once = patch_javascript(JS_FIXTURE)
        twice = patch_javascript(once)
        self.assertEqual(once, twice)
        self.assertIn(JS_MARKER, once)
        self.assertIn(SEAL_REASON_JS_MARKER, once)
        self.assertIn(SEAL_DETAIL_JS_MARKER, once)
        self.assertIn(OCR_REASON_JS_MARKER, once)
        self.assertEqual(once.count("await renderLabeledEvaluation();"), 1)

    def test_css_patch_is_idempotent(self):
        once = patch_css("body { color: black; }\n")
        twice = patch_css(once)
        self.assertEqual(once, twice)
        self.assertIn(CSS_MARKER, once)

    def test_find_web_files_chooses_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web = root / "web"
            web.mkdir()
            (web / "index.html").write_text(INDEX_FIXTURE, encoding="utf-8")
            (web / "app.js").write_text(JS_FIXTURE, encoding="utf-8")
            (web / "styles.css").write_text("body {}", encoding="utf-8")
            backup = root / ".backup_homepage_old" / "web"
            backup.mkdir(parents=True)
            (backup / "index.html").write_text(INDEX_FIXTURE, encoding="utf-8")
            self.assertEqual(find_web_files(root), (web / "index.html", web / "app.js", web / "styles.css"))


if __name__ == "__main__":
    unittest.main()
