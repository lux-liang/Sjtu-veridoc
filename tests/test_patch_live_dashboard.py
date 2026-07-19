import tempfile
import unittest
from pathlib import Path

from scripts.patch_live_dashboard import (
    BUSINESS_ACCEPTANCE_CSS_MARKER,
    BUSINESS_ACCEPTANCE_JS_MARKER,
    BUSINESS_ACCEPTANCE_MARKER,
    BUSINESS_MARKER,
    DUAL_EVAL_CSS_MARKER,
    DUAL_EVAL_JS_MARKER,
    EVAL_MARKER,
    SEAL_CLASS_DETAIL_JS_MARKER,
    TECHNICAL_PERSPECTIVE_CSS_MARKER,
    TECHNICAL_PERSPECTIVE_MARKER,
    VISUAL_REFRESH_CSS_MARKER,
    VISUAL_REFRESH_JS_MARKER,
    VISUAL_REFRESH_MARKER,
    find_web_files,
    patch_css,
    patch_index,
    patch_javascript,
)


INDEX = '''<html><body><main>
        <section class="metrics" id="metrics"></section>
        <section class="view" id="view-principles">
        <h2>五类风险检测原理</h2>
        <article class="panel pr-card c-blue"></article>
      </section>
    </main>
明鉴 · 材料真伪智能核验平台 view-principles
</body></html>
'''

JAVASCRIPT = '''
const REASON_META = {
  "visual:red_stamp_like_region": ["seal", "红章区域"],
};
function detail(row) {
  return [
    ["字体警告", row.font_warning_count],
  ];
}
const RISK_BANDS = [];
async function loadDashboard() {
  renderRiskDist(state.dashboard);
}
'''


class DashboardPatchTests(unittest.TestCase):
    def test_index_patch_is_idempotent(self):
        once = patch_index(INDEX)
        self.assertEqual(once, patch_index(once))
        self.assertIn(EVAL_MARKER, once)
        self.assertIn(BUSINESS_MARKER, once)
        self.assertIn(BUSINESS_ACCEPTANCE_MARKER, once)
        self.assertIn(TECHNICAL_PERSPECTIVE_MARKER, once)
        self.assertIn("五大业务分项准确度", once)
        self.assertIn("技术视角：五类检测证据", once)
        self.assertIn("双口径", once)
        self.assertIn(VISUAL_REFRESH_MARKER, once)
        self.assertIn("真实挑战口径 F1", once)

    def test_javascript_patch_is_idempotent(self):
        once = patch_javascript(JAVASCRIPT)
        self.assertEqual(once, patch_javascript(once))
        self.assertIn(DUAL_EVAL_JS_MARKER, once)
        self.assertIn(SEAL_CLASS_DETAIL_JS_MARKER, once)
        self.assertIn("marker_free_audit", once)
        self.assertIn(VISUAL_REFRESH_JS_MARKER, once)
        self.assertIn(BUSINESS_ACCEPTANCE_JS_MARKER, once)
        self.assertIn("renderCommandHero", once)
        self.assertIn("renderBusinessAcceptance", once)

    def test_css_patch_is_idempotent(self):
        once = patch_css("body {}")
        self.assertEqual(once, patch_css(once))
        self.assertIn(DUAL_EVAL_CSS_MARKER, once)
        self.assertIn(VISUAL_REFRESH_CSS_MARKER, once)
        self.assertIn(BUSINESS_ACCEPTANCE_CSS_MARKER, once)
        self.assertIn(TECHNICAL_PERSPECTIVE_CSS_MARKER, once)
        self.assertIn(".command-hero", once)

    def test_find_web_files_chooses_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web = root / "web"
            web.mkdir()
            (web / "index.html").write_text(INDEX, encoding="utf-8")
            (web / "app.js").write_text(JAVASCRIPT, encoding="utf-8")
            (web / "styles.css").write_text("body {}", encoding="utf-8")
            self.assertEqual(find_web_files(root)[0], web / "index.html")


if __name__ == "__main__":
    unittest.main()
