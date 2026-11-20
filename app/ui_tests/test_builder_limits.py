"""The standard's Limits step as the page draws it: its fields, their hints and the defaults they show."""
import json
import re
import subprocess

from ui_tests.builder_support import APP, CompilerCase


class LimitsStep(CompilerCase):
    def draw(self, key, edit=""):
        """standardForm on the Limits step and what it calls, cut from the page script, run against the core: its html;
        ``edit``, JavaScript changing the recipe ``r`` first."""
        page = (APP / "static/builder/command-builder.js").read_text(encoding="utf-8")
        cut = lambda start, end: page[page.index(start):page.index(end, page.index(start))]
        line = lambda name: re.search(r"^  const %s = .*$" % name, page, re.M).group(0) + "\n"
        schema = dict(self.schema, hosts=[], standardHere={"docker": None, "licences": {}, "host": {}})
        program = ("const schema=" + json.dumps(schema) + ";const C=require(" + json.dumps(str(APP / "static/builder/command-builder-core.js")) + ")(schema);\n"
                   + "const S=C.STEP,REVIEW=S.review;let step=S.limits;\n"
                   + line("esc") + line("button") + cut("  const unitsOf = ", "  // the first clause") + line("words")
                   + line("field") + line("fixed") + line("note") + cut("  function standardForm(", "  function compileRunner(")
                   + "const r=C.createStandard(" + json.dumps(key) + ");" + edit
                   + "process.stdout.write(JSON.stringify(standardForm(r)));")
        return json.loads(subprocess.run([self.node, "-"], input=program, check=True, capture_output=True, text=True).stdout)

    def test_attempts_and_retries_say_how_they_differ(self):
        html = self.draw("osworld1")
        self.assertIn('Attempts per task<input aria-label="Attempts per task" data-field="repeat"', html)
        self.assertIn("<small>Each task is run and scored this many times.</small>", html)
        self.assertIn('Retries after an error<input aria-label="Retries after an error" data-field="retries"', html)
        self.assertIn("<small>A try that ends in an error, such as a rate limit or a crash, is run again. A failed task never is.</small>", html)
        self.assertNotIn("Retries per attempt", html)
