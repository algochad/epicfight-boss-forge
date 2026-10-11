import _path  # noqa
import json
import py_compile
import re
import unittest

from _path import REPO, SKILL


def parse_frontmatter(text):
    lines = text.split("\n")
    assert lines[0] == "---", "SKILL.md must start with ---"
    end = lines.index("---", 1)
    fm, cur = {}, None
    for line in lines[1:end]:
        if not line.strip():
            continue
        if line.startswith("  ") and cur is not None:
            k, _, v = line.strip().partition(":")
            fm[cur][k.strip()] = v.strip().strip('"')
        else:
            k, _, v = line.partition(":")
            v = v.strip()
            if v == "":
                fm[k] = {}
                cur = k
            else:
                fm[k] = v[1:-1] if v.startswith('"') and v.endswith('"') else v
                cur = None
    return fm, "\n".join(lines[end + 1:])


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.fm, self.body = parse_frontmatter(self.text)

    def test_frontmatter_follows_agent_skills_spec(self):
        fm = self.fm
        self.assertEqual(set(fm), {"name", "description", "license", "compatibility", "metadata"})
        name = fm["name"]
        self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(name), 64)
        self.assertEqual(name, SKILL.name)
        self.assertTrue(1 <= len(fm["description"]) <= 1024)
        for word in ("boss", "Epic Fight", "Forge 1.20.1", "weapon", "texture"):
            self.assertIn(word, fm["description"])
        self.assertLessEqual(len(fm["compatibility"]), 500)
        self.assertTrue(fm["metadata"])
        self.assertTrue(all(isinstance(v, str) for v in fm["metadata"].values()))

    def test_body_size_and_referenced_paths(self):
        self.assertLess(len(self.text.split("\n")), 500)
        paths = set(re.findall(r"\b((?:references|scripts|assets)/[A-Za-z0-9_./-]+?)(?=[`\s),:;]|$)", self.body))
        self.assertTrue(paths)
        for p in paths:
            p = p.rstrip(".")
            self.assertTrue((SKILL / p).exists(), p)

    def test_plugin_manifests(self):
        plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
        market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
        self.assertRegex(plugin["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertFalse(plugin["name"].startswith(("claude-", "anthropic-")))
        self.assertEqual(plugin["name"], self.fm["name"])
        entry = market["plugins"][0]
        self.assertTrue(entry["source"].startswith("./"))
        self.assertEqual(entry["name"], plugin["name"])
        self.assertTrue(market["name"] and market["owner"]["name"])

    def test_codex_metadata_and_scripts_compile(self):
        y = (SKILL / "agents" / "openai.yaml").read_text()
        self.assertIn("interface:", y)
        self.assertIn("display_name:", y)
        for f in (SKILL / "scripts").rglob("*.py"):
            py_compile.compile(str(f), doraise=True, cfile=str(REPO / ".cache" / "pyc" / (f.name + "c")))


if __name__ == "__main__":
    unittest.main()
