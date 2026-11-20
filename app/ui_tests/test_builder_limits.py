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
                   + line("atMost") + line("field") + line("fixed") + line("note") + cut("  function standardForm(", "  function compileRunner(")
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

    def test_a_bound_of_the_benchmarks_shows_the_default_each_run_gets_and_its_label_names_it(self):
        steps = lambda html: re.search(r'<label class="nb-field" title="[^"]*">Steps per task[^<]*<input[^>]*data-bench-opt-select="max_steps"[^>]*>', html).group(0)
        agent = "r.others=[C.standardOthers(r).find(o=>!o.setup&&!o.control&&!o.why.length).key];"
        for edit, label, shown in ((agent, "Steps per task (default 100)", "100"),          # through the computer port
                                   ("r.others=['osworld1:m3'];", "Steps per task (default 100)", "100"),
                                   ("r.others=['osworld1:prompt'];", "Steps per task (default 15)", "15"),
                                   (agent + "r.others.push('osworld1:prompt');", "Steps per task<input", "default")):   # each its own
            with self.subTest(edit=edit):
                field = steps(self.draw("osworld1", edit))
                self.assertIn(label, field)
                self.assertIn(f'placeholder="{shown}"', field)

    def test_the_number_of_tasks_says_all_of_them_and_takes_that_many_at_most(self):
        field = lambda html: re.search(r'<label class="nb-field">Number of tasks<input[^>]*>(<small>[^<]*</small>)?', html).group(0)
        small = field(self.draw("osworld1", "r.datasets=['test_small'];"))
        self.assertIn('max="39"', small)
        self.assertIn('placeholder="39"', small)
        self.assertIn("<small>Empty: all 39. Else the first ones, in order.</small>", small)
        two = field(self.draw("osworld1", "r.datasets=['test_small','test_infeasible'];"))
        self.assertNotIn("max=", two)
        self.assertIn("<small>Empty: all of each dataset. Else the first ones, in order.</small>", two)

    def test_a_number_past_the_most_is_taken_as_the_most(self):
        page = (APP / "static/builder/command-builder.js").read_text(encoding="utf-8")
        line = re.search(r"^  const atMost = .*$", page, re.M).group(0)
        program = line + "\nconst f=m=>({max:m});process.stdout.write(JSON.stringify([atMost(f('39'),40),atMost(f('39'),39),atMost(f('39'),''),atMost(f(''),400)]));"
        got = json.loads(subprocess.run([self.node, "-"], input=program, check=True, capture_output=True, text=True).stdout)
        self.assertEqual(got, [39, 39, "", 400])
        self.assertIn("value=d.field==='limit'?atMost(el,typed):typed", page)           # what is typed in the field

    def test_what_the_record_keeps_of_the_model_calls_is_a_choice_wired_to_the_command_line(self):
        agent = "r.others=[C.standardOthers(r).find(o=>!o.setup&&!o.control&&!o.why.length).key];"
        pressed = lambda html: re.findall(r'data-std-exchanges="(\w+)" aria-pressed="true"', html)
        html = self.draw("osworld1", agent)
        self.assertEqual(re.findall(r'data-std-exchanges="(\w+)"', html), ["whole", "text", "off"])
        self.assertEqual(pressed(html), ["whole"])                         # pictures and text, by default
        for label in ("Pictures and text</strong><small>default</small>", "Text alone</strong>", "Nothing</strong>"):
            self.assertIn(label, html)
        self.assertEqual(pressed(self.draw("osworld1", agent + "r.exchanges='text';")), ["text"])
        self.assertNotIn("data-std-exchanges", self.draw("gsm8k", "r.others=['nop'];"))      # a run that calls no model keeps none
        # the choice stays in the recipe, and the run is told it
        kept = self.js("const r=C.fromJSON(JSON.stringify({...C.createStandard('osworld1'),exchanges:'off'}));process.stdout.write(JSON.stringify(r.exchanges));")
        self.assertEqual(kept, "off")
        from nullius.standard import recipe
        mine = {"version": 1, "harness": "standard", "name": "t", "title": "t", "benchmark": "gsm8k", "datasets": ["test"], "others": ["original"],
                "model_choice": "any", "provider": "openrouter", "model": "vendor/m", "limit": 1, "concurrency": 1, "where": "local"}
        for keep, flag in (("text", "--exchanges text"), ("off", "--exchanges off"), ("", None)):
            with self.subTest(keep=keep):
                out = recipe.compile(dict(mine, **({"exchanges": keep} if keep else {})))
                self.assertEqual(out["errors"], [])
                if flag:
                    self.assertIn(flag, out["script"])
                else:
                    self.assertNotIn("--exchanges", out["script"])
