"""gitmark regression tests (stdlib unittest): python3 -m unittest discover -s tests"""
import importlib.util
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gitmark", ROOT / "skills/kb-search/gitmark.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

OKF_DOC = textwrap.dedent("""\
    ---
    node_type: service          # REQUIRED — comment must not leak into the value
    title: "Billing: overview"
    service: [billing, api]
    links:
      documents: [../../src/billing]
      depends_on:
        - ../reference/arch.md
    generated: { by: claude-code/opus-5-5, at: 2026-09-27T22:53:05Z }
    verified:
      - { by: process:validate-format, at: 2026-09-28T09:01:00Z }
      - by: human:alice
        at: 2026-09-28T09:00:00Z
    stale_after: 2026-09-23T00:00:00Z
    notes: |
      line one
      line two
    ---
    # Billing
    """)


def write(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text), encoding="utf-8")


class Frontmatter(unittest.TestCase):
    def test_yaml_and_mini_parser_agree(self):
        full = g.parse_frontmatter(OKF_DOC)
        mini = g._mini_yaml(g.split_frontmatter(OKF_DOC)[0])
        self.assertEqual(full, mini)

    def test_fields(self):
        fm = g._mini_yaml(g.split_frontmatter(OKF_DOC)[0])
        self.assertEqual(fm["node_type"], "service")
        self.assertEqual(fm["title"], "Billing: overview")
        self.assertEqual(g.fm_links(fm), [("documents", "../../src/billing"),
                                          ("depends_on", "../reference/arch.md")])
        self.assertEqual(g.trust_tier(fm), "human")
        self.assertTrue(g.is_stale(fm))
        self.assertEqual(fm["notes"], "line one\nline two")

    def test_okf_type_alias_and_trust_tiers(self):
        self.assertEqual(g.node_type_of({"type": "Playbook"}), "Playbook")
        self.assertEqual(g.trust_tier({}), "unverified")
        self.assertEqual(g.trust_tier({"verified": {"by": "process:ci", "at": "x"}}), "machine")

    def test_not_quite_yaml_falls_back(self):
        self.assertEqual(g.parse_frontmatter("---\ntitle: a: b\nnode_type: plan\n---\n"),
                         {"title": "a: b", "node_type": "plan"})


class Search(unittest.TestCase):
    def test_stem_and_stopwords(self):
        self.assertEqual(g.stem("деплоймента"), "деплоймент")
        self.assertEqual(g.query_terms("Как задеплоить cache-router на стенд?"),
                         ["задеплоить", "cache-router", "стенд"])


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.root = Path(self.tmp.name)
        write(r, "docs/README.md", "# Docs\n- [Arch](reference/arch.md)\n")
        write(r, "docs/reference/README.md", "# Ref\n")
        write(r, "docs/reference/arch.md", """\
            ---
            node_type: reference
            title: Architecture
            description: How requests flow from the gateway to the billing service.
            status: deprecated
            ---
            # Architecture
            Gateway forwards to billing.
            """)
        write(r, "docs/reference/arch2.md", """\
            ---
            node_type: reference
            title: Architecture v2
            status: active
            links:
              supersedes: [arch.md]
              documents: [../../src/missing.py]
            ---
            # Architecture v2
            Gateway forwards to billing via the queue. See [old](arch.md) and [gone](nope.md).
            """)
        write(r, "notes/scratch.md", "# Scratch\nTODO: ask about billing.\n")
        write(r, "vendor-ish/lib.md", "# Vendored\nbilling gateway\n")
        write(r, ".gitignore", "vendor-ish/\n")
        subprocess.run(["git", "init", "-q", str(r)], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_discovery_respects_gitignore_and_gitmarkignore(self):
        write(self.root, ".gitmarkignore", "notes\n")
        rels = {p.relative_to(self.root).as_posix() for p in g.iter_md(self.root)}
        self.assertNotIn("vendor-ish/lib.md", rels)
        self.assertNotIn("notes/scratch.md", rels)
        self.assertIn("docs/reference/arch2.md", rels)

    def test_search_prefers_typed_live_doc(self):
        g.cmd_index(self.root)
        res = g.cmd_search(self.root, "billing gateway", 5)
        self.assertEqual(res[0]["path"], "docs/reference/arch2.md")
        self.assertEqual(res[0]["node_type"], "reference")
        only_dep = g.cmd_search(self.root, "billing", 5, statuses=["deprecated"])
        self.assertEqual([r["path"] for r in only_dep], ["docs/reference/arch.md"])
        self.assertEqual(g.cmd_search(self.root, "billing", 5, scope="history"), [])

    def test_lint_typed_links(self):
        codes = {(i[1], i[2]) for i in g.cmd_lint(self.root)["issues"]}
        self.assertNotIn(("I6", "docs/reference/arch2.md"), codes)     # target is deprecated
        self.assertIn(("I7", "docs/reference/arch2.md"), codes)        # src/missing.py
        self.assertIn(("I4", "docs/reference/arch2.md"), codes)        # nope.md

    def test_lint_entry_point(self):
        i9 = lambda: {(i[2], i[3].split(" ")[0]) for i in g.cmd_lint(self.root)["issues"] if i[1] == "I9"}
        self.assertEqual(i9(), {("AGENTS.md", "нет")})                       # docs/ есть, входа нет
        write(self.root, "CLAUDE.md", "# Rules\nsee [docs](docs/README.md)\n")
        self.assertEqual({p for p, _ in i9()}, {"CLAUDE.md"})              # всё в CLAUDE.md
        write(self.root, "AGENTS.md", "# Proj\n> Start: [docs/README.md](docs/README.md)\n")
        self.assertEqual({p for p, _ in i9()}, {"CLAUDE.md"})              # CLAUDE.md без импорта
        write(self.root, "CLAUDE.md", "# CLAUDE.md\n\n@AGENTS.md\n")
        self.assertEqual(i9(), set())
        write(self.root, "AGENTS.md", "# Proj\nno map\n")
        self.assertEqual({p for p, _ in i9()}, {"AGENTS.md"})              # нет ссылки на индекс
        write(self.root, "AGENTS.md", "# Proj\n> KB: [kb/README.md](kb/README.md)\n")
        self.assertEqual(i9(), set())                                      # БЗ в своей папке

    def test_summary_alias_and_frozen(self):
        write(self.root, "docs/reference/snap.md",
              "---\nnode_type: report\nsummary: Fleet snapshot on a date\nstatus: frozen\n"
              "links:\n  relates_to: [arch2.md]\n---\n# Snap\n")
        d = g.load_doc(self.root, self.root / "docs/reference/snap.md")
        self.assertEqual(d["meta"]["description"], "Fleet snapshot on a date")
        i2 = [i for i in g.cmd_lint(self.root)["issues"] if i[1] == "I2" and i[2] == "docs/reference/snap.md"]
        self.assertEqual(i2, [])

    def test_lint_baseline_ratchet(self):
        r = self.root
        git = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)
        git("add", "-A"); git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
        same = g.cmd_lint_baseline(r, "HEAD")
        self.assertFalse(same["grew"]); self.assertEqual(same["new"], [])
        write(r, "docs/reference/bad.md", "# Bad\nno frontmatter, see [x](missing.md)\n")
        grew = g.cmd_lint_baseline(r, "HEAD")
        self.assertTrue(grew["grew"])
        self.assertTrue(all(i[2] == "docs/reference/bad.md" for i in grew["new"]))
        self.assertIn("error", g.cmd_lint_baseline(r, "no-such-ref"))

    def test_toc_builds_index_block(self):
        r = self.root
        write(r, "docs/reference/README.md", "# Reference\n\nintro\n\n<!-- gitmark:toc -->\nold\n<!-- /gitmark:toc -->\n")
        check = g.cmd_toc(r)
        self.assertEqual(check["stale"], ["docs/reference/README.md"])
        g.cmd_toc(r, write=True)
        text = (r / "docs/reference/README.md").read_text()
        self.assertIn("intro", text)
        self.assertIn("- [Architecture v2](arch2.md)", text)
        self.assertNotIn("old", text)
        self.assertEqual(g.cmd_toc(r)["stale"], [])

    def test_secrets(self):
        r = self.root
        write(r, "docs/ops/keys.md", "token: ghp_" + "a" * 36 + "\npassword: <your-password>\napi_key=${API_KEY}\n")
        found = g.cmd_secrets(r)["found"]
        self.assertEqual([(f[0], f[1], f[2]) for f in found], [("docs/ops/keys.md", 1, "github-token"),
                                                             ("docs/ops/keys.md", 1, "assigned-secret")])
        self.assertTrue(all("a" * 10 not in f[3] for f in found))   # значение замаскировано

    def test_attrs(self):
        r = self.root
        self.assertEqual(len(g.cmd_attrs(r)["missing"]), len(g.UNION_LINES))
        g.cmd_attrs(r, write=True)
        self.assertEqual(g.cmd_attrs(r)["missing"], [])
        self.assertIn("merge=union", (r / ".gitattributes").read_text())

    def test_query(self):
        r = g.cmd_query(self.root, ["type=reference", "status!=deprecated"])
        self.assertEqual([x["path"] for x in r["rows"]], ["docs/reference/arch2.md"])
        r = g.cmd_query(self.root, ["description?"])
        self.assertEqual(r["matched"], 1)
        r = g.cmd_query(self.root, ["links.supersedes~arch"], count_by="status")
        self.assertEqual(r["counts"], [("active", 1)])


if __name__ == "__main__":
    unittest.main()
