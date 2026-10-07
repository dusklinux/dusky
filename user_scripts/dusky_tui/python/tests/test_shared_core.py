"""Shared router and rendering regressions; no real config writes or editors."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from python.frontend import core_types, ui
from python.frontend.core_types import ConfigItem
lazy from python.engines.json_engine import JsonEngine
lazy from python.engines.toml import TomlEngine
lazy from python.engines import cpu_core, pkg_throttle
lazy import json
lazy import tomllib
from textual.app import App

LAUNCHER = Path(__file__).resolve().parents[1] / "main/main.py"
spec = importlib.util.spec_from_file_location("dusky_router_tests", LAUNCHER)
router = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = router
spec.loader.exec_module(router)


class PoolTests(unittest.TestCase):
    def test_detachment_during_worker_binding_does_not_access_a_cleared_app(self):
        engine = object()
        pool = router.LazyEnginePool(lambda *_: engine)
        key = pool.register("fixture", "")

        class ClosingApp:
            @property
            def is_running(self):
                pool.detach_app()
                return True

            def call_from_thread(self, *args):
                raise RuntimeError("App is not running")

        pool._app = ClosingApp()
        pool._owner_thread = -1
        self.assertIs(pool[key], engine)
        self.assertEqual(list(pool.initialized_values()), [engine])

    def test_worker_binding_errors_are_not_suppressed_for_an_attached_app(self):
        pool = router.LazyEnginePool(lambda *_: object())
        key = pool.register("fixture", "")
        app = SimpleNamespace(is_running=True, call_from_thread=Mock(side_effect=RuntimeError("binding fixture")))
        pool._app = app
        pool._owner_thread = -1
        with self.assertRaisesRegex(RuntimeError, "binding fixture"):
            pool[key]

    def test_get_propagates_registered_factory_errors_and_allows_retry(self):
        instance = object()
        factory = Mock(side_effect=[KeyError("broken engine configuration"), instance])
        pool = router.LazyEnginePool(factory)
        key = pool.register("ini", "/fixture")
        with self.assertRaisesRegex(KeyError, "broken engine configuration"):
            pool.get(key)
        self.assertIn(key, pool)
        self.assertEqual(list(pool.initialized_values()), [])
        self.assertIs(pool.get(key), instance)
        self.assertEqual(factory.call_count, 2)

    def test_registration_and_mapping_operations_do_not_construct_unused_engines(self):
        factory = Mock(return_value=object())
        pool = router.LazyEnginePool(factory)
        key = pool.register("ini", "/fixture")
        self.assertEqual(list(pool.keys()), [key])
        self.assertEqual(len(pool), 1)
        self.assertIn(key, pool)
        self.assertEqual(list(pool.initialized_values()), [])
        self.assertIsNone(pool.get(("unknown", "")))
        factory.assert_not_called()
        with self.assertRaises(KeyError):
            pool[("unknown", "")]
        self.assertIs(dict(pool)[key], factory.return_value)
        self.assertIs(pool[key], factory.return_value)
        factory.assert_called_once_with(*key)
        del pool[key]
        self.assertNotIn(key, pool)
        pool.register(*key)
        pool.clear()
        self.assertEqual(len(pool), 0)
        self.assertEqual(list(pool), [])
        factory.assert_called_once()

    def test_concurrent_lookup_constructs_once(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def factory(*_):
            calls.append(object())
            started.set()
            if not release.wait(5):
                raise TimeoutError("factory never released")
            return calls[-1]
        pool = router.LazyEnginePool(factory)
        key = pool.register("ini", "/fixture")
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(pool.__getitem__, key)
            self.assertTrue(started.wait(5))
            second = executor.submit(pool.__getitem__, key)
            release.set()
            self.assertIs(first.result(5), second.result(5))
        self.assertEqual(len(calls), 1)


class PoolLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovered_unit_reads_include_retained_rows_and_isolate_targets(self):
        class Engine:
            target_path = ""
            def __init__(self):
                self.calls = []
            def load_state_for_units(self, user, system):
                self.calls.append((user, system))
                return {f"{scope}/{unit}": "true" for scope, units in (("user", user), ("system", system))
                        for unit in units}
            def load_state(self):
                raise AssertionError("unnecessary full unit scan")
        pool = router.LazyEnginePool(lambda *_: Engine())
        first = pool.register("fixture", "/first")
        retained = ConfigItem(label="Retained", key="first.service", scope="user", type_="bool", default=False)
        local = ConfigItem(label="Local", key="new.service", scope="user", type_="bool", default=False)
        remote = ConfigItem(label="Remote", key="second.service", scope="system", type_="bool", default=False,
                            target_file_override="/second")
        app = ui.DuskyTUI(pool, first, {0: [retained], 1: []}, ["Initial", "Discovered"],
                          enable_user_presets=False, deferred_load=lambda: ([1], {1: [local, remote]}))
        pool.bind_app(app)
        async with app.run_test() as pilot:
            async with asyncio.timeout(5):
                while not remote._initial_loaded:
                    await pilot.pause(.01)
            self.assertEqual(pool[first].calls[-1], (["first.service", "new.service"], []))
            self.assertEqual(pool[("fixture", "/second")].calls[-1], ([], ["second.service"]))
            self.assertIn("user/first.service", app._states[first])
            self.assertTrue(local.value)
            self.assertTrue(remote.value)

    async def test_unit_reads_are_isolated_by_engine_and_target(self):
        class Engine:
            def load_state_for_units(self, user, system):
                return {"user": user, "system": system}
        first, second = ("systemd", "/first"), ("systemd", "/second")
        items = [ConfigItem(label="First", key="first.service", scope="user", type_="bool", default=False),
                 ConfigItem(label="Second", key="second.service", scope="system", type_="bool", default=False,
                            target_file_override="/second")]
        app = ui.DuskyTUI({first: Engine(), second: Engine()}, first, {0: items}, ["Units"],
                          enable_user_presets=False, deferred_load=lambda: [])
        self.assertEqual(app._load_one_engine_sync(first), {"user": ["first.service"], "system": []})
        self.assertEqual(app._load_one_engine_sync(second), {"user": [], "system": ["second.service"]})

    async def test_discovery_waits_for_save_callbacks_before_publishing_state(self):
        class Engine:
            target_path = ""
            state = {}
            def load_state(self):
                return self.state.copy()
        key = ("fixture", "")
        engine = Engine()
        discovered = ConfigItem(label="New", key="y", type_="int", default=0)
        app = ui.DuskyTUI({key: engine}, key, {0: [], 1: []}, ["Initial", "Discovered"],
                          enable_user_presets=False)
        release = asyncio.Event()
        async def finish_save():
            await release.wait()
            engine.state["y"] = "9"
            app._bump_write_generation("fixture-save")
        async with app.run_test() as pilot:
            async with asyncio.timeout(5):
                while not app._boot_complete:
                    await pilot.pause(.01)
            app.deferred_load = lambda: ([1], {1: [discovered]}, {"y": "7"})
            app._deferred_started = True
            save = app._start_save_task(finish_save())
            worker = app._run_deferred_load(manual_refresh=True)
            try:
                await pilot.pause(.1)
                self.assertTrue(app._inventory_refreshing)
                self.assertEqual(app.schema[1], [])
                self.assertNotIn("y", app._states[key])
            finally:
                release.set()
                await save
            async with asyncio.timeout(5):
                await worker.wait()
            self.assertEqual(discovered.value, 9)
            self.assertEqual(app._states[key], {"y": "9"})
            self.assertFalse(app._inventory_refreshing)

    async def test_discovery_registers_and_loads_new_target(self):
        class Engine:
            target_path = ""
            def __init__(self, path):
                self.path = path
            def load_state(self):
                return {"y": "7"} if self.path == "/extra" else {}
        pool = router.LazyEnginePool(lambda _kind, path: Engine(path))
        key = pool.register("fixture", "")
        discovered = ConfigItem(label="New", key="y", type_="int", default=0,
                                target_file_override="/extra")
        app = ui.DuskyTUI(pool, key, {0: [], 1: []}, ["Initial", "Discovered"],
                          enable_user_presets=False, deferred_load=lambda: ([1], {1: [discovered]}))
        pool.bind_app(app)
        async with app.run_test() as pilot:
            async with asyncio.timeout(5):
                while not discovered._initial_loaded:
                    await pilot.pause(.01)
            self.assertEqual(discovered.value, 7)
            self.assertIn(("fixture", "/extra"), app._loaded_engines)
            self.assertEqual(app._states[("fixture", "/extra")], {"y": "7"})
            self.assertTrue(app.require_boot_complete())

    async def test_worker_creation_stays_off_ui_and_binding_runs_on_ui_thread(self):
        owner = threading.get_ident()
        calls = []
        class Engine:
            def set_app(self, app):
                calls.append(("bind", threading.get_ident()))
        def factory(*_):
            calls.append(("create", threading.get_ident()))
            return Engine()
        pool = router.LazyEnginePool(factory)
        key = pool.register("ini", "/fixture")
        app = App()
        async with app.run_test():
            pool.bind_app(app)
            engine = await asyncio.to_thread(pool.__getitem__, key)
            self.assertIs(pool[key], engine)
        self.assertEqual([kind for kind, _thread in calls], ["create", "bind"])
        self.assertNotEqual(calls[0][1], owner)
        self.assertEqual(calls[1][1], owner)

    async def test_shutdown_does_not_construct_unused_backend(self):
        class Engine:
            target_path = ""
            shutdown = Mock()
            def load_state(self):
                return {}
        factory = Mock(side_effect=lambda *_: Engine())
        pool = router.LazyEnginePool(factory)
        key = pool.register("ini", "")
        app = ui.DuskyTUI(pool, key, {0: []}, ["Initial"], enable_user_presets=False)
        async with app.run_test() as pilot:
            async with asyncio.timeout(5):
                while not app._boot_complete:
                    await pilot.pause(.01)
            pool.register("unused", "/fixture")
            pool.bind_app(app)
        factory.assert_called_once_with(*key)
        pool[key].shutdown.assert_called_once()


class UtilityTests(unittest.TestCase):
    def test_frozen_default_values_still_isolate_mutable_children(self):
        default = frozendict(nested=[1])
        item = ConfigItem(label="Frozen", key="frozen", type_="string", default=default)
        item.value["nested"].append(2)
        self.assertEqual(default["nested"], [1])
        self.assertEqual(item.value["nested"], [1, 2])

    def test_missing_state_sentinel_keeps_identity_when_cloned(self):
        self.assertIs(core_types.clone_value(ui._STATE_MISSING), ui._STATE_MISSING)
        self.assertIs(core_types.clone_value(ui._TARGET_UNREADABLE), ui._TARGET_UNREADABLE)

    def test_router_import_defers_ui_parser_and_cache_setup(self):
        code = (
            "import importlib.util,sys\n"
            "spec=importlib.util.spec_from_file_location('router_probe',sys.argv[1])\n"
            "module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module\n"
            "before=sys.pycache_prefix\n"
            "spec.loader.exec_module(module)\n"
            "assert sys.pycache_prefix==before\n"
            "assert 'argparse' not in sys.modules\n"
            "assert 'textual' not in sys.modules\n"
            "assert 'python.frontend.ui' not in sys.modules\n"
            "class MissingUI:\n"
            " def find_spec(self,name,*args):\n"
            "  if name=='python.frontend.ui': raise ModuleNotFoundError('UI fixture')\n"
            "sys.meta_path.insert(0,MissingUI())\n"
            "try: module.DuskyTUI()\n"
            "except ModuleNotFoundError as error: assert str(error)=='UI fixture'\n"
            "else: raise AssertionError('Missing UI did not fail on first use')\n"
        )
        result = subprocess.run([sys.executable, "-c", code, str(LAUNCHER)],
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_clone_preserves_custom_container_and_scalar_types(self):
        class CustomList(list):
            pass
        class CustomInt(int):
            pass
        for value in (CustomList([1, [2]]), CustomInt(3)):
            value.metadata = ["original"]
            copied = core_types.clone_value(value)
            self.assertIs(type(copied), type(value))
            self.assertIsNot(copied, value)
            copied.metadata.append("changed")
            self.assertEqual(value.metadata, ["original"])

    def test_hsl_overflow_returns_neutral_color(self):
        self.assertEqual(ui.color_to_rgb(f"hsl({'9' * 1000}, 50%, 50%)"), (128, 128, 128))

    def test_rgb_clamps_oversized_components_without_integer_conversion_failure(self):
        self.assertEqual(ui.color_to_rgb(f"rgb({'9' * 5000}, 0, 0)"), (255, 0, 0))
        self.assertEqual(ui.color_to_rgb(f"rgb({'0' * 5000}1, 2, 3)"), (1, 2, 3))
        self.assertEqual(ui.color_to_rgb("rgb(٠٠٠١, ٢, ٣)"), (1, 2, 3))

    def test_cached_option_tracks_read_only_changes(self):
        setting = ConfigItem(label="Value", key="value", type_="int", default=1)
        app = ui.DuskyTUI({}, ("ini", ""), {0: [setting]}, ["Initial"], enable_user_presets=False)
        self.assertNotIn("Read only", app._build_option(setting).plain)
        setting.read_only = True
        self.assertIn("Read only", app._build_option(setting).plain)

    def test_known_color_does_not_load_optional_database(self):
        core_types.is_theme_variable.cache_clear()
        with patch.object(core_types, "_get_css_named", side_effect=AssertionError("unnecessary import")):
            self.assertFalse(core_types.is_theme_variable("Red"))

    def test_special_css_colors_without_webcolors(self):
        original_import = __import__
        def without_webcolors(name, *args, **kwargs):
            if name == "webcolors":
                raise ModuleNotFoundError(name)
            return original_import(name, *args, **kwargs)
        core_types.is_theme_variable.cache_clear()
        with patch.object(core_types, "_css_named_cache", None), patch("builtins.__import__", side_effect=without_webcolors):
            for color in ("rebeccapurple", "transparent"):
                self.assertFalse(core_types.is_theme_variable(color))
        core_types.is_theme_variable.cache_clear()

    def test_hex_lengths_and_single_quote_character(self):
        self.assertTrue(core_types.is_theme_variable("0x1234567"))
        self.assertFalse(core_types.is_theme_variable("0x123456"))
        self.assertFalse(core_types.is_theme_variable("0x12345678"))
        setting = ConfigItem(label="Text", key="text", type_="string", default="")
        self.assertEqual(setting.deserialize('"'), '"')
        self.assertEqual(setting.deserialize('""'), "")

    def test_external_editor_routes_buttons_and_quoted_arguments(self):
        app = ui.DuskyTUI({}, ("ini", ""), {0: []}, ["Initial"], enable_user_presets=False)
        app.notify_status = Mock()
        app.run_suspended_interactive = Mock()
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "file with spaces.ini"
            file.touch()
            with patch.object(ui.shutil, "which", return_value="/usr/bin/xdg-open"), patch.object(ui.subprocess, "Popen") as popen:
                app.open_file_externally(file, button=1)
                popen.assert_called_once()
                self.assertEqual(popen.call_args.args[0], ["xdg-open", str(file)])
                app.run_suspended_interactive.assert_not_called()
            with patch.dict(os.environ, {"VISUAL": "editor --flag 'two words'"}):
                app.open_file_externally(file, button=3)
                app.run_suspended_interactive.assert_called_once_with(["editor", "--flag", "two words", str(file)])
        app.notify_status.assert_not_called()

    def test_cache_honors_interpreter_options(self):
        for options in (["-B"], ["-X", "pycache_prefix=/tmp/dusky-audit-explicit-cache"]):
            result = subprocess.run(
                [sys.executable, *options, "-c", "import runpy,sys; before=(sys.dont_write_bytecode,sys.pycache_prefix); runpy.run_path(sys.argv[1]); assert before==(sys.dont_write_bytecode,sys.pycache_prefix)", str(LAUNCHER)],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_escalation_notice_does_not_contaminate_export_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            schema = Path(directory) / "root_schema.py"
            schema.write_text("SCHEMA={0:[]}\nTABS=['Initial']\nTARGET_FILE='/tmp/dusky-root-fixture'\n"
                              "ENGINE_TYPE='ini'\nREQUIRE_ROOT=True\n", encoding="utf-8")
            code = (
                "import runpy,sys; from unittest.mock import patch; sys.argv=sys.argv[1:]\n"
                "with patch('os.geteuid',return_value=1000),patch('shutil.which',return_value='/usr/bin/sudo'),"
                "patch('os.execvp',side_effect=SystemExit(0)):\n"
                " runpy.run_path(sys.argv[0],run_name='__main__')\n"
            )
            result = subprocess.run([sys.executable, "-u", "-c", code, str(LAUNCHER), str(schema), "--export-state"],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertIn("Escalating", result.stderr)

    def test_failed_headless_batch_is_not_replayed_as_individual_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, calls, schema = (root / name for name in ("config.ini", "calls", "schema.py"))
            target.write_text("[main]\nvalue=1\n", encoding="utf-8")
            for message in ("Partial batch failure", "AUTH_REQUIRED"):
                with self.subTest(message=message):
                    calls.unlink(missing_ok=True)
                    schema.write_text(
                        "from pathlib import Path\n"
                        "from python.frontend.core_types import ConfigItem\n"
                        "from python.engines.ini import IniConfigEngine\n"
                        f"calls=Path({str(calls)!r})\n"
                        "def batch(self, changes):\n"
                        "    calls.write_text('batch\\n')\n"
                        f"    return False, {message!r}, ''\n"
                        "def single(self, *args, **kwargs):\n"
                        "    with calls.open('a') as out: out.write('replayed\\n')\n"
                        "    return True, '', ''\n"
                        "IniConfigEngine.write_batch=batch\nIniConfigEngine.write_value=single\n"
                        f"TARGET_FILE={str(target)!r}\nENGINE_TYPE='ini'\nTABS=['Values']\n"
                        "SCHEMA={0:[ConfigItem(label='Value', key='value', scope='main', type_='int', default=2)]}\n",
                        encoding="utf-8",
                    )
                    result = subprocess.run([sys.executable, str(LAUNCHER), str(schema), "--default"],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
                    self.assertEqual(calls.read_text(), "batch\n")
                    self.assertIn(message, result.stdout)

    def test_headless_router_round_trip_across_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second, schema = (root / name for name in ("first.ini", "second.ini", "schema.py"))
            first.write_text("[main]\ncount=1\nflag=false\nreadonly=7\n", encoding="utf-8")
            second.write_text("[main]\nother=10\n", encoding="utf-8")
            schema.write_text(
                "from python.frontend.core_types import ConfigItem\n"
                f"TARGET_FILE={str(first)!r}\nENGINE_TYPE='ini'\nENABLE_USER_PRESETS=False\n"
                "TABS=['Values']\nSCHEMA={0:[\n"
                "ConfigItem(label='Count', key='count', scope='main', type_='int', default=2),\n"
                "ConfigItem(label='Flag', key='flag', scope='main', type_='bool', default=True),\n"
                "ConfigItem(label='Readonly', key='readonly', scope='main', type_='int', default=0, read_only=True),\n"
                f"ConfigItem(label='Other', key='other', scope='main', type_='int', default=9, target_file_override={str(second)!r}),\n"
                "]}\n", encoding="utf-8",
            )
            def invoke(*args):
                result = subprocess.run([sys.executable, str(LAUNCHER), str(schema), *args],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                return result.stdout
            self.assertIn("Read only", invoke("--export-docs"))
            invoke("--set", "main.count=3")
            self.assertIn("count=3", first.read_text(encoding="utf-8"))
            invoke("--set", "flag=off")
            self.assertIn("flag=false", first.read_text(encoding="utf-8"))
            invoke("--reset-key", "count")
            self.assertIn("count=2", first.read_text(encoding="utf-8"))
            invoke("--default")
            self.assertIn("readonly=7", first.read_text(encoding="utf-8"))
            self.assertIn("other=9", second.read_text(encoding="utf-8"))
            import json
            state = json.loads(invoke("--export-state"))
            self.assertEqual(state["main/count"], "2")
            self.assertEqual(state["main/flag"], "true")
            self.assertEqual(next(value for key, value in state.items() if key.endswith("::main/other")), "9")
            for flag in ("--set", "--reset-key"):
                result = subprocess.run([sys.executable, str(LAUNCHER), str(schema), flag, ""],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 1, result.stderr + result.stdout)


class HardwareEngineLifecycleTests(unittest.TestCase):
    def test_cpu_atomic_write_resolves_deferred_dependencies_on_first_use(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested/config"
            cpu_core.atomic_write(path, "fixture\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "fixture\n")
            self.assertEqual(list(path.parent.iterdir()), [path])
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)

    def test_shutdown_closes_energy_descriptors_while_engine_remains_referenced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "energy_uj"
            path.write_text("12345\n", encoding="utf-8")
            for module, cls in ((cpu_core, cpu_core.CpuCoreEngine),
                                (pkg_throttle, pkg_throttle.PkgThrottleEngine)):
                with self.subTest(engine=cls.__name__):
                    engine = cls.__new__(cls)
                    engine.reader = module.FastEnergyReader(path)
                    descriptor = engine.reader.fd
                    self.assertIsNotNone(descriptor)
                    self.assertEqual(engine.reader.read(), 12345)
                    try:
                        engine.shutdown()
                        with self.assertRaises(OSError):
                            os.fstat(descriptor)
                        engine.shutdown()
                        self.assertIsNone(engine.reader.fd)
                    finally:
                        engine.reader.close()


class FileEngineTests(unittest.TestCase):
    def test_real_file_engines_round_trip_through_headless_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for kind in ("json", "toml"):
                with self.subTest(engine=kind):
                    target = root / f"config.{kind}"
                    schema = root / f"schema_{kind}.py"
                    schema.write_text(
                        "from python.frontend.core_types import ConfigItem\n"
                        f"TARGET_FILE={str(target)!r}\nENGINE_TYPE={kind!r}\nTABS=['Values']\n"
                        "ENABLE_USER_PRESETS=False\n"
                        "SCHEMA={0:[ConfigItem(label='Large',key='large',type_='int',default=0)]}\n",
                        encoding="utf-8",
                    )
                    for args in (("--set", "large=9007199254740993"), ("--export-state",)):
                        result = subprocess.run([sys.executable, str(LAUNCHER), str(schema), *args],
                                                capture_output=True, text=True, encoding="utf-8", timeout=10)
                        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    self.assertEqual(json.loads(result.stdout)["large"], 9007199254740993)
                    original = target.read_bytes()
                    result = subprocess.run([sys.executable, str(LAUNCHER), str(schema), "--set", "large=invalid"],
                                            capture_output=True, text=True, encoding="utf-8", timeout=10)
                    self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
                    self.assertEqual(target.read_bytes(), original)

    def test_importing_file_and_hardware_engines_defers_unused_dependencies(self):
        expected = {
            "json_engine": ("json", "tempfile"),
            "toml": ("json", "tempfile", "tomllib"),
            "cpu_core": ("json", "tempfile", "subprocess"),
            "pkg_throttle": ("json", "math", "python.engines.cpu_core"),
        }
        code = (
            "import importlib,sys\n"
            "from python.frontend import core_types\n"
            "before=set(sys.modules)\n"
            "module=importlib.import_module('python.engines.'+sys.argv[1])\n"
            "assert not (set(sys.argv[2:]) & (set(sys.modules)-before))\n"
            "class MissingDependency:\n"
            " def find_spec(self,name,*args):\n"
            "  if name==sys.argv[2]: raise ModuleNotFoundError('deferred fixture')\n"
            "sys.meta_path.insert(0,MissingDependency())\n"
            "name=sys.argv[2].split('.')[-1]\n"
            "try: getattr(getattr(module,name),'loads' if name=='json' else 'flock')\n"
            "except ModuleNotFoundError as error: assert str(error)=='deferred fixture'\n"
            "else: raise AssertionError('Dependency failure did not occur at first use')\n"
        )
        for module, dependencies in expected.items():
            with self.subTest(module=module):
                result = subprocess.run([sys.executable, "-c", code, module, *dependencies],
                                        capture_output=True, text=True, encoding="utf-8", timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_json_commit_closes_temporary_file_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"x": 1}', encoding="utf-8")
            files = []
            original_factory = tempfile.NamedTemporaryFile

            def track_file(*args, **kwargs):
                stream = original_factory(*args, **kwargs)
                files.append(stream)
                return stream

            with patch("python.engines.json_engine.tempfile.NamedTemporaryFile", side_effect=track_file), \
                 patch("python.engines.json_engine.json.dump", side_effect=TypeError("serialization fixture")):
                self.assertFalse(JsonEngine(str(path)).write_value("x", "DEFAULT", "2", "int")[0])
            self.assertTrue(files[0].closed)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"x": 1}')
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_jsonc_preserves_strings_while_removing_comments_and_trailing_commas(self):
        expected = {"message": 'comma,} and comma,] and "quoted" // text',
                    "escaped": '\\" // text', "url": "https://example.com/a/*b*/", "values": [1, 2]}
        encoded = json.dumps(expected)
        content = "/* header */ " + encoded[:-1] + ", // trailing\n}\n"
        self.assertEqual(json.loads(JsonEngine._strip_json_comments(content)), expected)
        self.assertEqual(json.loads(JsonEngine._strip_json_comments('{"values": [1, 2, /* note */ ],}')),
                         {"values": [1, 2]})
        with self.assertRaises(json.JSONDecodeError):
            json.loads(JsonEngine._strip_json_comments('{"value": 1/* note */2}'))

    def test_json_write_keeps_an_unparseable_or_nonobject_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            engine = JsonEngine(str(path))
            for content in ('{"broken":', '[1, 2]', 'null'):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    self.assertFalse(engine.write_value("x", "DEFAULT", "1", "int")[0])
                    self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_file_engines_reject_invalid_numbers_without_applying_any_batch_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            for cls, suffix, content in ((JsonEngine, "json", '{"x": 1}'), (TomlEngine, "toml", "x = 1\n")):
                path = Path(directory) / f"config.{suffix}"
                engine = cls(str(path))
                for value, kind in (("invalid", "int"), ("inf", "int"), ("invalid", "float")):
                    with self.subTest(engine=cls.__name__, value=value, kind=kind):
                        path.write_text(content, encoding="utf-8")
                        result = engine.write_batch([("x", "DEFAULT", "2", "int"),
                                                     ("y", "DEFAULT", value, kind)])
                        self.assertFalse(result[0])
                        self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_file_engines_keep_large_integers_exact_and_accept_decimal_integer_input(self):
        with tempfile.TemporaryDirectory() as directory:
            for cls, suffix in ((JsonEngine, "json"), (TomlEngine, "toml")):
                with self.subTest(engine=cls.__name__):
                    engine = cls(str(Path(directory) / f"config.{suffix}"))
                    result = engine.write_batch([("large", "DEFAULT", "9007199254740993", "int"),
                                                 ("decimal", "DEFAULT", "3.0", "int")])
                    self.assertTrue(result[0], result[1])
                    state = engine.load_state()
                    self.assertEqual(state["large"], 9007199254740993)
                    self.assertEqual(state["decimal"], 3)

    def test_toml_round_trip_preserves_unicode_keys_values_dates_arrays_and_empty_tables(self):
        import datetime
        data = {"emoji😀": "😀", "bad\n": "value", "empty": {}, "parent": {"leaf": {}},
                "date": datetime.date(2026, 10, 7), "items": [{"emoji😀": "😀"}, {}],
                "nested": {"x": 1}, "controls": "\b\t\n\f\r\x00"}
        self.assertEqual(tomllib.loads(TomlEngine._dump_toml(data)), data)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[empty]\n[parent.leaf]\n', encoding="utf-8")
            engine = TomlEngine(str(path))
            self.assertTrue(engine.write_value("message", "DEFAULT", "😀")[0])
            self.assertEqual(tomllib.loads(path.read_text(encoding="utf-8")),
                             {"message": "😀", "empty": {}, "parent": {"leaf": {}}})

    def test_file_engines_report_read_errors_and_leave_targets_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            for cls, suffix, content in ((JsonEngine, "json", '{"x": 1}'), (TomlEngine, "toml", "x = 1\n")):
                with self.subTest(engine=cls.__name__):
                    path = Path(directory) / f"config.{suffix}"
                    path.write_text(content, encoding="utf-8")
                    engine = cls(str(path))
                    with patch("builtins.open", side_effect=PermissionError("read fixture")):
                        self.assertFalse(engine.write_value("x", "DEFAULT", "2", "int")[0])
                    self.assertEqual(path.read_text(encoding="utf-8"), content)
                    path.write_bytes(b'x = "\xff"\n')
                    self.assertFalse(engine.write_value("x", "DEFAULT", "2", "int")[0])
                    self.assertEqual(path.read_bytes(), b'x = "\xff"\n')

    def test_toml_conflicting_table_path_returns_failure_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text("x = 1\n", encoding="utf-8")
            result = TomlEngine(str(path)).write_value("y", "x", "2", "int")
            self.assertFalse(result[0])
            self.assertEqual(path.read_text(encoding="utf-8"), "x = 1\n")


class _StartupEngine:
    target_path = ""

    def load_state(self):
        return {"x": "1"}

class PoolStartupTests(unittest.IsolatedAsyncioTestCase):
    async def boot(self, app, pilot):
        async with asyncio.timeout(5):
            while not app._boot_complete:
                await pilot.pause(.01)

    async def test_shutdown_drains_cancelled_telemetry_before_closing_backend(self):
        started, release = threading.Event(), threading.Event()
        calls = []

        class TelemetryEngine(_StartupEngine):
            def get_telemetry(self):
                started.set()
                if not release.wait(5):
                    raise TimeoutError("telemetry fixture was not released")
                calls.append("telemetry finished")
                return "Fixture telemetry"

            def shutdown(self):
                calls.append("shutdown")

        pool = router.LazyEnginePool(lambda *_: TelemetryEngine())
        key = pool.register("fixture", "")
        app = ui.DuskyTUI(pool, key, {0: []}, ["Values"], enable_user_presets=False)
        pool.bind_app(app)
        telemetry_task = None
        try:
            async with asyncio.timeout(8):
                async with app.run_test() as pilot:
                    await self.boot(app, pilot)
                    telemetry_task = asyncio.create_task(app.update_telemetry())
                    while not started.is_set():
                        await pilot.pause(.005)
                    telemetry_task.cancel()
                    app.exit()

                    async def finish_telemetry():
                        await asyncio.sleep(.05)
                        release.set()

                    asyncio.create_task(finish_telemetry())
                await asyncio.gather(telemetry_task, return_exceptions=True)
                self.assertEqual(calls, ["telemetry finished", "shutdown"])
                self.assertFalse(app._io_workers)
        finally:
            release.set()
            if telemetry_task is not None:
                await asyncio.gather(telemetry_task, return_exceptions=True)

    async def test_initial_constructor_runs_on_worker_and_binding_on_ui(self):
        owner = threading.get_ident()
        calls = []

        class BoundEngine(_StartupEngine):
            def set_app(self, app):
                calls.append(("bind", threading.get_ident()))

        def factory(*_):
            calls.append(("create", threading.get_ident()))
            time.sleep(.05)
            return BoundEngine()

        pool = router.LazyEnginePool(factory)
        key = pool.register("fixture", "")
        item = ConfigItem(label="X", key="x", type_="int", default=0)
        app = ui.DuskyTUI(pool, key, {0: [item]}, ["Values"], enable_user_presets=False)
        pool.bind_app(app)
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            self.assertEqual(item.value, 1)
        self.assertEqual([name for name, _ in calls], ["create", "bind"])
        self.assertNotEqual(calls[0][1], owner)
        self.assertEqual(calls[1][1], owner)

    async def test_initial_constructor_failure_is_reported_without_crashing_ui(self):
        pool = router.LazyEnginePool(Mock(side_effect=RuntimeError("constructor fixture")))
        key = pool.register("fixture", "")
        item = ConfigItem(label="X", key="x", type_="int", default=0)
        app = ui.DuskyTUI(pool, key, {0: [item]}, ["Values"], enable_user_presets=False)
        pool.bind_app(app)
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            self.assertIn("constructor fixture", app._failed_engines[key])
            self.assertFalse(app.require_boot_complete())
            self.assertFalse(app.query_one(ui.FileLink).path)
            self.assertEqual(list(pool.initialized_values()), [])

    async def test_custom_only_tab_uses_loaded_engine_target_and_telemetry(self):
        class TelemetryEngine(_StartupEngine):
            target_path = "/fixture/actual-target"

            def get_telemetry(self):
                return "Fixture telemetry"

        pool = router.LazyEnginePool(lambda *_: TelemetryEngine())
        key = pool.register("fixture", "/fixture/registered-target")
        app = ui.DuskyTUI(pool, key, {0: []}, ["Custom"], enable_user_presets=False,
                       custom_views={0: lambda: "Fixture view"})
        pool.bind_app(app)
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            self.assertEqual(app.query_one(ui.FileLink).path, "/fixture/actual-target")
            self.assertIs(app.telemetry_engine, pool[key])
            await app.update_telemetry()
            self.assertTrue(app.query_one("#telemetry-banner").display)

    async def test_file_link_does_not_construct_an_unloaded_engine(self):
        factory = Mock(return_value=_StartupEngine())
        pool = router.LazyEnginePool(factory)
        key = pool.register("fixture", "")
        app = ui.DuskyTUI(pool, key, {0: []}, ["Values"], enable_user_presets=False)
        link = SimpleNamespace(path="old target")
        app.query_one = lambda *_: link
        app._update_file_link()
        factory.assert_not_called()
        self.assertEqual(link.path, "")

    async def test_quit_during_construction_drains_and_shuts_down_backend(self):
        started, release = threading.Event(), threading.Event()
        calls = []

        class ClosingEngine(_StartupEngine):
            def set_app(self, app):
                calls.append("bind")

            def shutdown(self):
                calls.append("shutdown")

        def factory(*_):
            started.set()
            if not release.wait(5):
                raise TimeoutError("constructor fixture was not released")
            calls.append("constructed")
            return ClosingEngine()

        pool = router.LazyEnginePool(factory)
        key = pool.register("fixture", "")
        item = ConfigItem(label="X", key="x", type_="int", default=0)
        app = ui.DuskyTUI(pool, key, {0: [item]}, ["Values"], enable_user_presets=False)
        pool.bind_app(app)
        async with asyncio.timeout(8):
            async with app.run_test() as pilot:
                while not started.is_set():
                    await pilot.pause(.005)
                app.exit()

                async def finish_constructor():
                    await asyncio.sleep(.05)
                    release.set()

                asyncio.create_task(finish_constructor())
            self.assertEqual(calls[-1], "shutdown")
            self.assertEqual(calls.count("shutdown"), 1)
            self.assertFalse(app._io_workers)

    async def test_save_completion_refreshes_only_initialized_backends(self):
        engine = _StartupEngine()
        engine.refresh_after_write = True
        engine.cache = {"x": "2"}
        factory = Mock(return_value=engine)
        pool = router.LazyEnginePool(factory)
        key = pool.register("fixture", "")
        pool[key]
        pool.register("unused", "/fixture")
        app = ui.DuskyTUI(pool, key, {0: []}, ["Values"], enable_user_presets=False)
        app._apply_refreshed_states = Mock()
        task = asyncio.create_task(asyncio.sleep(0))
        await task
        app._on_save_task_done(task)
        factory.assert_called_once_with(*key)
        app._apply_refreshed_states.assert_called_once_with({key: engine.cache})

    async def test_missing_batch_result_stays_pending_and_reports_failure(self):
        class PartialEngine(_StartupEngine):
            write_value = Mock(side_effect=AssertionError("must not replay"))

            def write_batch_results(self, changes):
                return {("x", "DEFAULT"): SimpleNamespace(ok=True, message="", actual="2")}

        key = ("fixture", "")
        rows = [ConfigItem(label=name, key=name, type_="int", default=1) for name in ("x", "y")]
        app = ui.DuskyTUI({key: PartialEngine()}, key, {0: rows}, ["Values"],
                       enable_user_presets=False, default_mode="batch")
        app._save_lock = asyncio.Lock()
        app.notify_status = Mock()
        app.play_reset_sound = Mock()
        app._refresh_all_ui = Mock()
        app._refresh_presets_ui = Mock()
        for row in rows:
            row.value = 2
        app.pending_commits = {(0, 0), (0, 1)}
        completed = []
        await app._save_batch_async(completed.append)
        self.assertEqual(completed, [False])
        self.assertEqual(app.pending_commits, {(0, 1)})
        self.assertTrue(app._save_failure_pending)
        self.assertIn("Missing write result", app.notify_status.call_args.args[0])
        app.engine_pool[key].write_value.assert_not_called()

class HeadlessRoutingTests(unittest.TestCase):
    def test_schema_search_paths_keep_order_and_expand_home_on_demand(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            locations = (home / "user_scripts", home / ".config/dusky_schema", home / "Documents/schemas")
            paths = []
            for index, location in enumerate(locations):
                path = location / "pkg/fixture.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "SCHEMA={0:[]}\nTABS=['Values']\nTARGET_FILE=''\nENGINE_TYPE='ini'\n"
                    f"APP_TITLE='Search fixture {index}'\n", encoding="utf-8",
                )
                paths.append(path)
            env = {**os.environ, "HOME": str(home), "XDG_CACHE_HOME": str(home / "cache")}
            for index, path in enumerate(paths):
                with self.subTest(location=index):
                    argument = "pkg.fixture" if index != 1 else "pkg/fixture.py"
                    result = subprocess.run([sys.executable, str(LAUNCHER), argument, "--export-docs"],
                                            env=env, capture_output=True, text=True, encoding="utf-8", timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f"Search fixture {index}", result.stdout)
                    path.unlink()
            result = subprocess.run([sys.executable, str(LAUNCHER), "pkg.fixture", "--export-docs"],
                                    env=env, capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(result.returncode, 1)
            for location in locations:
                self.assertIn(str(location), result.stdout)

    def invoke(self, rows, *args, batch="none"):
        with tempfile.TemporaryDirectory() as directory:
            schema = Path(directory) / "schema.py"
            schema.write_text(
                "import sys, types\n"
                "from python.frontend.core_types import ConfigItem\n"
                "from types import SimpleNamespace\n"
                "TARGET_FILE=''\nENGINE_TYPE='ini'\nTABS=['Values']\n"
                f"SCHEMA={{0:[{rows}]}}\n"
                "class Engine:\n"
                " def __init__(self,**kwargs): print('CONSTRUCTED')\n"
                " def load_state(self): return {}\n"
                " def write_value(self,key,scope,value,**kwargs):\n"
                "  print('WROTE',key,scope,value)\n"
                "  return True,'written',''\n"
                " def write_batch_results(self,changes):\n"
                "  print('BATCH')\n"
                f"  selected=changes if {batch!r}=='complete' else changes[:1] if {batch!r}=='partial' else []\n"
                "  return {(k,s):SimpleNamespace(ok=True,message='',actual=v) for k,s,v,t in selected}\n"
                "module=types.ModuleType('python.engines.ini')\n"
                "module.IniConfigEngine=Engine\nsys.modules[module.__name__]=module\n",
                encoding="utf-8",
            )
            return subprocess.run([sys.executable, str(LAUNCHER), str(schema), *args],
                                  capture_output=True, text=True, encoding="utf-8", timeout=10)

    def test_read_only_single_key_operations_reject_before_backend_construction(self):
        rows = "ConfigItem(label='Read only',key='readonly',type_='int',default=0,read_only=True)"
        for args in (("--set", "readonly=9"), ("--reset-key", "readonly")):
            with self.subTest(args=args):
                result = self.invoke(rows, *args)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("read only", result.stdout)
                self.assertNotIn("CONSTRUCTED", result.stdout)
                self.assertNotIn("WROTE", result.stdout)

    def test_longest_ambiguous_key_never_selects_a_shorter_key(self):
        rows = ("ConfigItem(label='Short',key='x',type_='string',default=''),"
                "ConfigItem(label='A',key='x=y',scope='a',type_='string',default=''),"
                "ConfigItem(label='B',key='x=y',scope='b',type_='string',default='')")
        result = self.invoke(rows, "--set", "x=y=value")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("ambiguous", result.stdout)
        self.assertNotIn("WROTE", result.stdout)
        for argument, expected in (("a.x=y=value", "WROTE x=y a value"),
                                   ("x=a=b", "WROTE x DEFAULT a=b")):
            with self.subTest(argument=argument):
                result = self.invoke(rows, "--set", argument)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)

    def test_missing_headless_batch_results_return_failure_without_replaying(self):
        rows = ",".join(f"ConfigItem(label='{k}',key='{k}',type_='int',default=1)" for k in ("x", "y"))
        for batch, applied in (("none", 0), ("partial", 1)):
            with self.subTest(batch=batch):
                result = self.invoke(rows, "--default", batch=batch)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(f"{applied} applied", result.stdout)
                self.assertIn("Missing write result", result.stdout)
                self.assertEqual(result.stdout.count("BATCH"), 1)
                self.assertNotIn("WROTE", result.stdout)
        result = self.invoke(rows, "--default", batch="complete")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Reset 2 items successfully", result.stdout)
