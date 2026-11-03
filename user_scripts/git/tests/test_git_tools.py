"""Isolated regression tests: python -B -m unittest discover -s tests -v.

All commits, resets and pushes are confined to temporary local repositories.
"""
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
saved_environment = os.environ.copy()
spec = importlib.util.spec_from_file_location('manager', ROOT / 'git_dusky.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
os.environ.clear()
os.environ.update(saved_environment)
from rich.console import Console
m.console = Console(file=io.StringIO(), color_system=None)

class BareRepoCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dusky-test-')
        self.root = Path(self.tmp.name)
        self.w = self.root / 'home'
        self.w.mkdir()
        self.g = self.root / 'repo'
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(HOME=str(self.root / 'config-home'), XDG_CONFIG_HOME=str(self.root / 'config-home/.config'), GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1', GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid', GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid')
        subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(self.g)], env=self.env, check=True, stdout=subprocess.DEVNULL)
        (self.root / 'config-home').mkdir()
        self.env.update(GIT_DIR=str(self.g), GIT_WORK_TREE=str(self.w))
        self.envpatch = patch.dict(os.environ, self.env, clear=True)
        self.envpatch.start()
        m.GIT_DIR = self.g
        m.WORK_TREE = self.w
        m.DOTFILES_LIST = self.w / '.manifest'
        self.write('a', 'base\n')
        self.write('b', 'base\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial')

    def tearDown(self):
        self.envpatch.stop()
        self.tmp.cleanup()

    def git(self, *a, check=True):
        return subprocess.run(['git', *a], env=self.env, cwd=self.w, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def write(self, n, t):
        p = self.w / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(t)

    def commit(self, entries):
        with patch.object(m, 'ask', return_value='test'), patch.object(m, 'ask_yesno', return_value=False):
            m.stage_entries(entries, local_only=True)

class SelectedRestoreTests(BareRepoCase):

    def test_action_numbers_follow_display_order(self):
        self.assertEqual([a.key for a in m.ACTIONS], [str(n) for n in range(1, 18)])
        self.assertEqual([a.category for a in m.ACTIONS], sorted(a.category for a in m.ACTIONS))
        self.assertIs(m.ACTION_MAP['17'].handler, m.run_time_machine)

    def test_time_machine_bypasses_parent_repository_lock(self):
        from dataclasses import replace
        with patch.object(m, 'run_time_machine') as handler:
            action = replace(m.ACTION_MAP['17'], handler=handler)
            with patch.dict(m.ACTION_MAP, {'17': action}), patch.object(m.fcntl, 'flock', side_effect=AssertionError('parent must not lock Time Machine')):
                self.assertTrue(m.dispatch('17'))
                handler.assert_called_once()

    def test_picker_directory_colors_match_gitdelta(self):
        cache = {}
        names = ['.config/hypr/source/keybinds.lua', '.config/zshrc/git',
                 'user_scripts/arch_setup_scripts/scripts/setup.sh',
                 'user_scripts/git/git_dusky.py', 'user_scripts/rofi/calculator.sh']
        for name, color in zip(names, [111, 176, 215, 114, 221], strict=True):
            display = m.format_path_colored(name, cache)
            self.assertTrue(display.startswith(f'\x1b[38;5;{color}m'))
            self.assertEqual(m.strip_ansi(display), name)
        display = m.format_path_colored('user_scripts/git/tests/test_git_tools.py', cache)
        self.assertEqual(display, '\x1b[38;5;114muser_scripts/git/\x1b[2mtests/\x1b[0mtest_git_tools.py')
        self.assertEqual(len(cache), 5)
        self.assertEqual(m.format_path_colored('.zshrc', cache), '.zshrc')

    def restore(self, *names):
        def select(rows, **kwargs):
            import json
            self.assertTrue(kwargs['multi'])
            self.assertIn('discard selected staged + unstaged', kwargs['header'])
            return [row for row in rows if json.loads(row.split('\t', 1)[0]) in names]
        with patch.object(m, 'fzf_select', side_effect=select):
            return m.restore_selected_files()

    def test_selected_staged_and_unstaged_preserves_other_files(self):
        self.write('a', 'staged\n')
        self.write('b', 'other staged\n')
        self.git('add', 'a', 'b')
        self.write('a', 'unstaged\n')
        self.write('b', 'other unstaged\n')
        self.write('untracked', 'keep\n')
        index_b = self.git('ls-files', '-s', 'b')
        head = self.git('rev-parse', 'HEAD')
        self.restore('a')
        self.assertEqual((self.w / 'a').read_text(), 'base\n')
        self.assertEqual(self.git('diff', 'HEAD', '--', 'a'), b'')
        self.assertEqual(self.git('ls-files', '-s', 'b'), index_b)
        self.assertEqual((self.w / 'b').read_text(), 'other unstaged\n')
        self.assertEqual((self.w / 'untracked').read_text(), 'keep\n')
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_multiselect_deletions_and_staged_addition(self):
        (self.w / 'a').unlink()
        (self.w / 'b').unlink()
        self.git('add', '-u', 'b')
        self.write('new', 'added\n')
        self.git('add', 'new')
        self.restore('a', 'b', 'new')
        self.assertEqual((self.w / 'a').read_text(), 'base\n')
        self.assertEqual((self.w / 'b').read_text(), 'base\n')
        self.assertFalse((self.w / 'new').exists())
        self.assertEqual(self.git('status', '--porcelain'), b'')

    def test_rename_restores_both_paths(self):
        self.git('mv', 'a', 'renamed')
        self.write('renamed', 'edits after rename\n')
        self.write('b', 'keep\n')
        self.restore('renamed')
        self.assertEqual((self.w / 'a').read_text(), 'base\n')
        self.assertFalse((self.w / 'renamed').exists())
        self.assertEqual((self.w / 'b').read_text(), 'keep\n')
        self.assertEqual(self.git('status', '--porcelain'), b' M b\n')

    def test_literal_filenames(self):
        names = ['[x]', 'x', 'line\nbreak', 'tab\tfile', '-option', ':(glob)*']
        for name in names:
            self.write(name, 'base\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'names')
        for name in names:
            self.write(name, 'changed\n')
        self.restore(*[name for name in names if name != 'x'])
        for name in names:
            self.assertEqual((self.w / name).read_text(), 'changed\n' if name == 'x' else 'base\n')

    def test_cancel_and_untracked_only_do_nothing(self):
        self.write('a', 'keep\n')
        before = self.git('status', '--porcelain=v1', '-z')
        self.restore()
        self.assertEqual(self.git('status', '--porcelain=v1', '-z'), before)
        self.assertEqual((self.w / 'a').read_text(), 'keep\n')
        self.restore('a')
        self.write('new', 'keep\n')
        with patch.object(m, 'fzf_select') as picker:
            m.restore_selected_files()
        picker.assert_not_called()
        self.assertEqual((self.w / 'new').read_text(), 'keep\n')

    def test_manifest_limits_choices(self):
        m.DOTFILES_LIST.write_text('a\n')
        self.write('a', 'changed\n')
        self.write('b', 'keep\n')
        self.restore('a', 'b')
        self.assertEqual((self.w / 'a').read_text(), 'base\n')
        self.assertEqual((self.w / 'b').read_text(), 'keep\n')

    def test_unborn_head_does_not_open_picker(self):
        self.git('symbolic-ref', 'HEAD', 'refs/heads/unborn')
        before = self.git('ls-files', '-s')
        with patch.object(m, 'fzf_select') as picker:
            self.assertFalse(m.restore_selected_files())
        picker.assert_not_called()
        self.assertEqual(self.git('ls-files', '-s'), before)
        self.assertEqual((self.w / 'a').read_text(), 'base\n')

    def test_action_registered_and_restore_failure_reported(self):
        self.assertIs(m.ACTION_MAP['10'].handler, m.restore_selected_files)
        self.assertTrue(m.ACTION_MAP['10'].destructive)
        self.write('a', 'keep\n')
        (self.g / 'index.lock').touch()
        with patch.object(m, 'select_changed_entries', return_value=[('a', None, ' M')]):
            self.assertFalse(m.dispatch('10'))
        self.assertEqual((self.w / 'a').read_text(), 'keep\n')


class GitTests(BareRepoCase):

    def test_selected_preserves_other_index(self):
        self.write('a', 'chosen\n')
        self.write('b', 'staged\n')
        self.git('add', 'b')
        self.write('b', 'unstaged\n')
        before = self.git('ls-files', '-s', 'b')
        self.commit([e for e in m.changed_entries() if e[0] == 'a'])
        self.assertEqual(self.git('show', 'HEAD:a'), b'chosen\n')
        self.assertEqual(self.git('show', 'HEAD:b'), b'base\n')
        self.assertEqual(before, self.git('ls-files', '-s', 'b'))
        self.assertEqual((self.w / 'b').read_text(), 'unstaged\n')

    def test_literal_and_unusual_names(self):
        names = ['[x]', 'x', 'colon: a', 'line\nbreak', '\x1b[31mred', 'unicodé', '-option', ':(glob)*']
        for n in names:
            self.write(n, 'one')
        self.git('add', '.')
        self.git('commit', '-qm', 'names')
        for n in names:
            self.write(n, 'two')
        self.git('add', '--', 'x')
        self.commit([e for e in m.changed_entries() if e[0] == '[x]'])
        self.assertEqual(self.git('show', 'HEAD:[x]'), b'two')
        self.assertEqual(self.git('show', 'HEAD:x'), b'one')
        self.commit(m.changed_entries())
        self.assertEqual(self.git('status', '--porcelain'), b'')

    def test_staged_and_unstaged_delete(self):
        (self.w / 'a').unlink()
        self.git('add', '-u', 'a')
        (self.w / 'b').unlink()
        self.commit(m.changed_entries())
        self.assertEqual(self.git('ls-tree', '--name-only', 'HEAD'), b'')

    def test_added_then_deleted(self):
        self.write('new', 'x')
        self.git('add', 'new')
        (self.w / 'new').unlink()
        self.commit(m.changed_entries())
        self.assertEqual(self.git('status', '--porcelain'), b'')

    def test_rename_modified(self):
        self.git('mv', 'a', 'renamed')
        self.write('renamed', 'new')
        self.commit(m.changed_entries())
        self.assertEqual(self.git('show', 'HEAD:renamed'), b'new')
        self.assertEqual(self.git('status', '--porcelain'), b'')

    def test_empty_manifest_discard(self):
        self.write('.manifest', '')
        self.write('a', 'keep')
        before = self.git('status', '--porcelain')
        with patch.object(m, 'ask_yesno', side_effect=AssertionError('must not prompt')):
            m.discard_local_changes()
        self.assertEqual(before, self.git('status', '--porcelain'))

    def test_manifest_preserves_unlisted(self):
        self.write('.manifest', 'a\n')
        self.write('a', 'chosen')
        self.write('b', 'other')
        self.git('add', 'b')
        with patch.object(m, 'ask', return_value='scoped'):
            m.sync_all(local_only=True)
        self.assertEqual(self.git('show', 'HEAD:b'), b'base\n')
        self.assertEqual(self.git('show', ':b'), b'other')
        self.assertIn(b'b', self.git('ls-files'))

    def test_raw_filename(self):
        path = os.fsencode(self.w) + b'/bad-\xff'
        with open(path, 'wb') as file:
            file.write(b'x')
        self.commit(m.changed_entries())
        self.assertIn(b'bad-\xff', self.git('ls-files', '-z'))

    def test_missing_manifest_tracked_only(self):
        self.write('a', 'new')
        self.write('new', 'untouched')
        with patch.object(m, 'ask', return_value='tracked'):
            m.sync_all(local_only=True)
        self.assertEqual(self.git('show', 'HEAD:a'), b'new')
        self.assertEqual(self.git('ls-files', 'new'), b'')

    def test_empty_initial_commit(self):
        self.git('symbolic-ref', 'HEAD', 'refs/heads/empty')
        self.git('read-tree', '--empty')
        self.commit([('a', None, '??')])
        self.assertEqual(self.git('show', 'HEAD:a'), b'base\n')

    def test_isolated_env(self):
        os.environ['GIT_INDEX_FILE'] = str(self.root / 'other-index')
        os.environ['GIT_GLOB_PATHSPECS'] = '1'
        self.write('[foo]', 'yes')
        self.commit([('[foo]', None, '??')])
        self.assertEqual(self.git('show', 'HEAD:[foo]'), b'yes')

class AuditTests(BareRepoCase):
    def test_manifest_rename_keeps_both_sides(self):
        self.git('mv', 'a', 'outside')
        for scope in ['a', 'outside']:
            self.write('.manifest', scope + '\n')
            self.assertEqual(m.scoped_entries(), [('outside', 'a', 'R ')])
        self.commit(m.scoped_entries())
        self.assertEqual(self.git('show', 'HEAD:outside'), b'base\n')
        self.assertEqual(self.git('ls-tree', '--name-only', 'HEAD', 'a'), b'')

    def preview(self, name):
        with patch.object(m.shutil, 'which', return_value=None), patch('sys.stdout', new_callable=io.StringIO) as out:
            m.handle_diff_preview('', name, [])
            return out.getvalue()

    def test_preview_preserves_literal_whitespace_names(self):
        for name in ['[x]', 'x', ' spaced ']:
            self.write(name, 'base\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'names')
        self.write('[x]', 'chosen-marker\n')
        self.write('x', 'wrong-marker\n')
        self.write(' spaced ', 'space-marker\n')
        self.assertIn('chosen-marker', self.preview('[x]'))
        self.assertNotIn('wrong-marker', self.preview('[x]'))
        self.assertIn('space-marker', self.preview(' spaced '))

    def test_preview_staged_addition_and_non_utf8_content(self):
        self.write('new', 'staged-marker\n')
        self.git('add', 'new')
        self.assertIn('staged-marker', self.preview('new'))
        (self.w / 'new').unlink()
        self.assertEqual(self.preview('new'), '')
        (self.w / 'raw').write_bytes(b'non-utf8-\xff\n')
        self.assertIn('non-utf8-', self.preview('raw'))

    def test_numstat_rename_and_control_names(self):
        self.git('mv', 'a', 'renamed')
        self.write('renamed', 'edited\n')
        self.write('tab\tline\nbreak', 'different content\n')
        self.git('add', '.')
        stats = m.get_numstat_map()
        self.assertIn('renamed', stats)
        self.assertIn('tab\tline\nbreak', stats)

    def test_python_picker_roundtrips_control_names(self):
        names = ['tab\tname', 'line\nbreak', '[literal]', ' spaced ']
        for name in names:
            self.write(name, 'new')
        def select(choices, **kwargs):
            self.assertIn('--diff-preview-json', kwargs['preview'])
            return choices
        with patch.object(m, 'fzf_select', side_effect=select), patch.object(m, 'stage_entries') as stage:
            m.sync_single()
        self.assertEqual({entry[0] for entry in stage.call_args.args[0]}, set(names))

    def test_json_preview_cli(self):
        import json
        (self.w / 'dusky').symlink_to(self.g, target_is_directory=True)
        name = 'tab\tline\nbreak'
        self.write(name, 'preview-cli-marker\n')
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'git_dusky.py'),
            '--diff-preview-json', '', json.dumps(name)], env=self.env | {'HOME': str(self.w)},
            capture_output=True, check=True)
        self.assertIn(b'preview-cli-marker', result.stdout)

    def test_preview_batches_tracked_files(self):
        self.write('a', 'edited-a')
        self.write('b', 'edited-b')
        with patch.object(m, 'run_git', wraps=m.run_git) as calls, patch.object(m.shutil, 'which', return_value=None), patch('sys.stdout', new_callable=io.StringIO):
            m.handle_diff_preview('', 'a', ['a', 'b'])
        self.assertEqual(calls.call_count, 3)

    def test_shell_selection_roundtrip(self):
        config = ROOT.parents[1] / '.config/zshrc/git'
        self.git('mv', 'a', ' spaced ')
        self.git('commit', '-qm', 'rename')
        result = subprocess.run(['zsh', '-fc',
            'source "$1"; function fzf() { printf "enter\\0"; /usr/bin/cat; }; function _gitdelta_show() { printf "%s\\0" "$@"; }; _gitdelta_select 1',
            'audit', str(config)], env=self.env | {'HOME': str(self.w), 'DUSKY_GIT_DIR': str(self.g)}, capture_output=True, check=True)
        self.assertIn(b' spaced ', result.stdout.split(b'\0'))

    def test_shell_staging_failure_propagates(self):
        config = ROOT.parents[1] / '.config/zshrc/git'
        self.write('.manifest', 'a\n')
        result = subprocess.run(['zsh', '-fc',
            'source "$1"; DUSKY_GIT_LIST="$HOME/.manifest"; function git_dusky() { return 7; }; git_dusky_add_list',
            'audit', str(config)], env=self.env | {'HOME': str(self.w)}, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_shell_selector_uses_literal_paths(self):
        config = ROOT.parents[1] / '.config/zshrc/git'
        names = ['unicodé', 'with|pipe', 'line\nbreak', 'tab\tfile', '\x1b[31mescape', ' spaced ']
        for name in names:
            self.write(name, 'new\n')
        self.git('add', '.')
        result = subprocess.run(['zsh', '-fc',
            'source "$1"; _gitdelta_render_stream "" fzf_items', 'audit', str(config)],
            env=self.env | {'HOME': str(self.w), 'DUSKY_GIT_DIR': str(self.g)}, capture_output=True, check=True)
        paths = [record.split(b'\t', 1)[1] for record in result.stdout.split(b'\0') if record]
        for name in names:
            self.assertIn(name.encode(), paths)

class SecondPassTests(BareRepoCase):
    def shell(self, script, *, env=None, input_data=b''):
        config = ROOT.parents[1] / '.config/zshrc/git'
        return subprocess.run(['zsh', '-fc', 'source "$1"\n' + script, 'test', str(config)],
            env=self.env | {'HOME': str(self.w), 'DUSKY_GIT_DIR': str(self.g)} | (env or {}),
            input=input_data, capture_output=True)

    def test_shell_manifest_home_prefixes(self):
        self.write('one', 'first')
        self.write('two', 'second')
        self.write('.manifest', '$HOME/one\n~/two\n')
        result = self.shell('DUSKY_GIT_LIST="$HOME/.manifest"; git_dusky_add_list')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('ls-files', '-z', 'one', 'two'), b'one\0two\0')

    def test_shell_git_errors_are_not_clean_results(self):
        for script in ['_gitdelta_render_stream nonexistent fzf_items',
                       '_gitdelta_render_stream nonexistent', '_gitdelta_show nonexistent',
                       'git_dusky symbolic-ref HEAD refs/heads/unborn; gitdelta a']:
            with self.subTest(script=script):
                result = self.shell(script)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(b'no changed files', result.stdout + result.stderr)

    def test_shell_diff_failure_stops_before_commit(self):
        result = self.shell('''function git_dusky() {
            if [[ $1 == diff ]]; then return 23; fi
            if [[ $1 == commit ]]; then print UNEXPECTED_COMMIT; fi
            return 0
        }
        git_dusky_push "$HOME/a"''', input_data=b'message\n')
        self.assertEqual(result.returncode, 23)
        self.assertNotIn(b'UNEXPECTED_COMMIT', result.stdout)
        self.assertNotIn(b'Commit message for', result.stderr)

    def test_diff_stream_failure_and_pager_close_status(self):
        bindir = self.root / 'bin'
        bindir.mkdir()
        renderer = bindir / 'delta'
        renderer.write_text('#!/bin/sh\n/usr/bin/cat >/dev/null\nexit "$TEST_RENDERER"\n')
        renderer.chmod(0o755)
        for producer, consumer, expected in [(7, 0, 7), (0, 9, 9), (141, 0, 0), (0, 141, 141)]:
            with self.subTest(producer=producer, consumer=consumer):
                result = self.shell('function _gitdelta_render_stream() { return $TEST_PRODUCER; }; _gitdelta_show',
                    env={'TEST_PRODUCER': str(producer), 'TEST_RENDERER': str(consumer),
                         'PATH': str(bindir) + os.pathsep + self.env['PATH']})
                self.assertEqual(result.returncode, expected, result.stderr)

    def test_editor_arguments_and_surviving_selected_file(self):
        self.write('b', 'one\ntwo\nthree\n')
        self.git('add', 'b')
        self.git('commit', '-qm', 'lines')
        self.write('b', 'one\nchanged\nthree\n')
        (self.w / 'a').unlink()
        editor = self.root / 'editor with space'
        output = self.root / 'editor-args'
        editor.write_text('#!/bin/sh\nprintf "%s\\0" "$@" > "$EDITOR_OUTPUT"\n')
        editor.chmod(0o755)
        result = self.shell('function fzf() { printf "ctrl-e\\0"; /usr/bin/cat; }; _gitdelta_select',
            env={'EDITOR': f'"{editor}" --flag', 'EDITOR_OUTPUT': str(output)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_bytes().split(b'\0')[:-1],
                         [b'--flag', b'+2', os.fsencode(self.w / 'b')])

    def test_directory_symlink_preview(self):
        (self.w / 'directory-link').symlink_to('.')
        with patch.object(m.shutil, 'which', return_value=None), patch('sys.stdout', new_callable=io.StringIO) as output:
            m.handle_diff_preview('', 'directory-link', [])
        self.assertIn("'directory-link' → '.'", output.getvalue())

    def test_file_disappearing_during_preview_reports_error(self):
        self.write('vanishes', 'contents')
        original = m.run_git
        def run(*args, **kwargs):
            if args[0] == 'diff' and '--no-index' in args:
                (self.w / 'vanishes').unlink()
            return original(*args, **kwargs)
        with patch.object(m, 'run_git', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'File preview failed'):
                m.handle_diff_preview('', 'vanishes', [])

    def test_preview_cli_uses_configured_repo_and_validates_arguments(self):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'git_dusky.py'),
            '--diff-preview', '', 'a'], env=self.env | {'HOME': str(self.w), 'DUSKY_GIT_DIR': str(self.g)},
            capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for args in [['--move-preview', 'invalid'], ['--diff-preview'], ['3', 'extra'],
                     ['--key-escape', 'extra'], ['--diff-preview-json', '', 'bad-json']]:
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, '-B', str(ROOT / 'git_dusky.py'), *args],
                    env=self.env | {'HOME': str(self.w), 'DUSKY_GIT_DIR': str(self.g)}, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(b'Traceback', result.stdout + result.stderr)

    def test_failed_action_sets_cli_exit_status(self):
        with patch.object(m, 'check_dependencies'), patch.object(m, 'sync_all', return_value=False), patch('sys.argv', ['dusky', '3']):
            with self.assertRaises(SystemExit) as exit_status:
                m.main()
        self.assertEqual(exit_status.exception.code, 1)
        with patch.object(m, 'sync_all', side_effect=RuntimeError('failed')):
            self.assertFalse(m.dispatch('3'))

    def test_failed_commit_hook_sets_failure_result(self):
        self.write('a', 'edit')
        hook = self.g / 'hooks/pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        before = self.git('rev-parse', 'HEAD')
        with patch.object(m, 'ask', return_value='message'):
            self.assertFalse(m.stage_entries([('a', None, ' M')], local_only=True))
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)

    def test_detached_step_back_leaves_local_state(self):
        self.write('a', 'second')
        self.git('add', 'a')
        self.git('commit', '-qm', 'second')
        self.git('checkout', '--detach')
        self.write('a', 'private edits')
        before = self.git('rev-parse', 'HEAD')
        with patch.object(m, 'ask_yesno', side_effect=AssertionError('must check branch first')):
            self.assertFalse(m.quick_step_back())
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual((self.w / 'a').read_text(), 'private edits')

    def test_force_push_recovery_sets_new_upstream(self):
        remote = self.root / 'remote'
        subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(remote)],
            env={k: v for k, v in self.env.items() if k not in ('GIT_DIR', 'GIT_WORK_TREE')},
            check=True, stdout=subprocess.DEVNULL)
        self.git('remote', 'add', 'origin', str(remote))
        self.git('push', 'origin', 'main')
        old = self.git('rev-parse', 'HEAD').decode().strip()
        self.write('a', 'remote')
        self.git('add', 'a')
        self.git('commit', '-qm', 'remote')
        self.git('push', 'origin', 'main')
        self.git('reset', '--hard', old)
        self.write('b', 'local')
        self.git('add', 'b')
        self.git('commit', '-qm', 'local')
        with patch.object(m, 'ask_yesno', return_value=True), patch.object(m, 'ask', return_value='2'):
            self.assertTrue(m.safe_push())
        self.assertEqual(m.upstream_target('main'), ('origin', 'refs/heads/main'))

    def test_sha256_initial_selected_commit_preserves_other_index(self):
        self.g = self.root / 'sha256'
        subprocess.run(['git', 'init', '--bare', '--object-format=sha256', '--initial-branch=main', str(self.g)],
            env={k: v for k, v in self.env.items() if k not in ('GIT_DIR', 'GIT_WORK_TREE')},
            check=True, stdout=subprocess.DEVNULL)
        self.env['GIT_DIR'] = str(self.g)
        m.GIT_DIR = self.g
        self.git('add', 'a', 'b')
        before = self.git('ls-files', '-s', 'b')
        self.commit([('a', None, 'A ')])
        self.assertEqual(self.git('ls-tree', '--name-only', 'HEAD'), b'a\n')
        self.assertEqual(self.git('show', 'HEAD:a'), b'base\n')
        self.assertEqual(self.git('ls-files', '-s', 'b'), before)


class LayoutTests(unittest.TestCase):

    def test_direction_steps_bounds_and_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory)
            layout = settings / 'git_preview_layout'
            def call(action, direction):
                return subprocess.run(['bash', str(ROOT / 'git_fzf_layout.sh'), action, direction, directory],
                    env=os.environ | {'FZF_PREVIEW_COLUMNS': '1', 'FZF_COLUMNS': '140'},
                    check=True, capture_output=True, text=True).stdout
            for edge, grow, shrink in [('right', 'left', 'right'), ('left', 'right', 'left'),
                                       ('up', 'down', 'up'), ('down', 'up', 'down')]:
                layout.write_text(f'{edge},70%,wrap-word')
                for expected in (75, 80, 85, 90):
                    self.assertIn(f'{edge},{expected}%,wrap-word', call('--resize-preview', grow))
                self.assertEqual(call('--resize-preview', grow), '')
                for _ in range(16):
                    call('--resize-preview', shrink)
                self.assertEqual(layout.read_text().strip(), f'{edge},10%,wrap-word')
                self.assertEqual(call('--resize-preview', shrink), '')
                call('--move-preview', 'hidden')
                self.assertEqual(layout.read_text().strip(), 'hidden')
                self.assertEqual(call('--resize-preview', grow), '')
                call('--move-preview', 'hidden')
                self.assertEqual(layout.read_text().strip(), f'{edge},10%,wrap-word')
            call('--move-preview', 'up')
            self.assertEqual(layout.read_text().strip(), 'up,50%,border-bottom,wrap')


class SecondPassTerminalTests(BareRepoCase):

    def wait_layout(self, expected, child):
        import pexpect
        import time
        layout = self.w / '.config/dusky/settings/git_preview_layout'
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if layout.is_file() and layout.read_text().strip() == expected:
                return
            # Drain redraws while waiting; a full PTY output buffer blocks fzf.
            try:
                child.read_nonblocking(size=65536, timeout=0.01)
            except pexpect.TIMEOUT:
                pass
        self.fail(f'Layout did not become {expected}: {layout.read_text() if layout.exists() else "missing"}')
    def terminal(self, executable, args):
        try:
            import pexpect
        except ImportError:
            self.skipTest('pexpect is required for terminal tests')
        env = self.env | {'HOME': str(self.w), 'DUSKY_GIT_DIR': str(self.g), 'TERM': 'xterm-256color',
            'FZF_DEFAULT_OPTS': '--height=10% --bind=enter:abort --preview=echo BAD',
            'FZF_DEFAULT_OPTS_FILE': '/does/not/exist'}
        child = pexpect.spawn(executable, args, env=env, encoding='utf-8', timeout=10, dimensions=(40,140))
        self.addCleanup(child.close, force=True)
        return child, pexpect

    def test_python_search_escape_then_quit(self):
        code = f"import runpy; m=runpy.run_path({str(ROOT / 'git_dusky.py')!r}); print('RESULT',repr(m['fzf_select'](['alpha','beta'])))"
        child, pexpect = self.terminal(sys.executable, ['-B', '-c', code])
        child.expect_exact('\x1b[?1049h')
        child.send('\x1b[1;1R')
        child.expect_exact('q:quit')
        child.send('/')
        child.expect_exact('search ❯')
        child.send('alpha\x1b')
        child.expect_exact('q:quit')
        child.send('\x1b')
        child.expect_exact('RESULT []')
        child.expect(pexpect.EOF)
        child.close()
        self.assertEqual(child.exitstatus, 0)

    def test_real_fzf_repeated_resize_keys(self):
        code = f"import runpy; m=runpy.run_path({str(ROOT / 'git_dusky.py')!r}); print('RESULT',repr(m['fzf_select'](['alpha'],preview='printf RESIZE_READY')))"
        child, pexpect = self.terminal(sys.executable, ['-B', '-c', code])
        child.expect_exact('\x1b[?1049h')
        child.send('\x1b[1;1R')
        child.expect_exact('q:quit')
        child.expect_exact('RESIZE_READY')
        for size in (75, 80, 85):
            child.send('\x1b[1;3D')
            self.wait_layout(f'right,{size}%,border-left,wrap', child)
        for size in (80, 75):
            child.send('\x1b[1;3C')
            self.wait_layout(f'right,{size}%,border-left,wrap', child)
        child.send('q')
        child.expect_exact('RESULT []')
        child.expect(pexpect.EOF)
        child.close()
        self.assertEqual(child.exitstatus, 0)

    def test_selected_restore_through_real_fzf(self):
        self.write('a', 'RESTORE_PREVIEW_MARKER\n')
        self.write('b', 'keep other edits\n')
        head = self.git('rev-parse', 'HEAD')
        child, pexpect = self.terminal(sys.executable, ['-B', str(ROOT / 'git_dusky.py'), '10'])
        child.expect_exact('\x1b[?1049h')
        child.send('\x1b[1;1R')
        child.expect_exact('q:quit')
        child.expect_exact('RESTORE_PREVIEW_MARKER')
        child.send('\r')
        child.expect(pexpect.EOF)
        self.assertIn('Restored 1 selected change(s) to HEAD.', m.strip_ansi(child.before))
        child.close()
        self.assertEqual(child.exitstatus, 0)
        self.assertEqual((self.w / 'a').read_text(), 'base\n')
        self.assertEqual((self.w / 'b').read_text(), 'keep other edits\n')
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_shell_real_preview_and_selection_preserve_control_names(self):
        name = ' spaced\tline\nbreak\x1b[31m '
        self.write(name, 'old\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'name')
        self.write(name, 'TERMINAL_PREVIEW_MARKER\n')
        self.write('.git_dusky_list', '')
        helper = self.w / 'user_scripts/git/git_dusky.py'
        helper.parent.mkdir(parents=True)
        helper.symlink_to(ROOT / 'git_dusky.py')
        helper.with_name('git_fzf_layout.sh').symlink_to(ROOT / 'git_fzf_layout.sh')
        config = ROOT.parents[1] / '.config/zshrc/git'
        child, pexpect = self.terminal('zsh', ['-fc', 'source "$1"; _gitdelta_select; print -r -- "RESULT=$?"', 'test', str(config)])
        child.expect_exact('\x1b[?1049h')
        child.send('\x1b[1;1R')
        child.expect_exact('TERMINAL_PREVIEW_MARKER')
        for size in (75, 80):
            child.send('\x1b[1;3D')
            self.wait_layout(f'right,{size}%,border-left,wrap', child)
        child.send('\r')
        child.expect_exact('RESULT=0')
        child.expect(pexpect.EOF)
        child.close()
        self.assertEqual(child.exitstatus, 0)


class TimeMachineTests(BareRepoCase):

    def run_tm(self, script):
        env = self.env | {'HOME': str(self.w), 'DUSKY_SOURCED': '1', 'DUSKY_PERSIST_DIR': str(self.root / 'persist'), 'DUSKY_RUN_ROOT': str(self.root / 'run'), 'DUSKY_SESSION_DIR': str(self.root / 'session'), 'DUSKY_SETTINGS_DIR': str(self.root / 'settings'), 'DUSKY_TM_ENGINE': str(ROOT / 'time_machine/dusky_time_machine_tui.sh')}
        prefix = 'source "$DUSKY_TM_ENGINE"\n_dusky_bind_paths\n_dusky_bind_colors\n_dusky_state_init\n_dusky_load_present_target\n'
        p = subprocess.run(['bash', '--noprofile', '--norc', '-c', prefix + script], env=env, cwd=self.w, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def history(self):
        old = self.git('rev-parse', 'HEAD').decode().strip()
        self.write('a', 'present\n')
        self.git('add', 'a')
        self.git('commit', '-qm', 'present')
        return old

    def test_tm_roundtrip_index(self):
        old = self.history()
        self.write('a', 'staged\n')
        self.git('add', 'a')
        self.write('a', 'unstaged\n')
        self.write('secret', 'private')
        index = self.git('ls-files', '-s')
        status = self.git('status', '--porcelain=v1', '-z')
        self.run_tm(f'_dusky_git_checkout {old} || exit 10\n_dusky_git_return || exit 11\n')
        self.assertEqual(index, self.git('ls-files', '-s'))
        self.assertEqual(status, self.git('status', '--porcelain=v1', '-z'))
        self.assertEqual((self.w / 'a').read_text(), 'unstaged\n')
        self.assertEqual(self.git('stash', 'list'), b'')

    def test_tm_failed_trip_restores_stash(self):
        self.write('collision', 'historical')
        self.git('add', 'collision')
        self.git('commit', '-qm', 'old')
        old = self.git('rev-parse', 'HEAD').decode().strip()
        self.git('rm', 'collision')
        self.git('commit', '-qm', 'present')
        self.write('collision', 'private')
        self.write('a', 'edits')
        self.git('add', 'a')
        idx = self.git('ls-files', '-s')
        self.run_tm(f'if _dusky_git_checkout {old}; then exit 20; fi\n[[ "$(_dusky_read stash)" == none ]] || exit 21\n')
        self.assertEqual((self.w / 'collision').read_text(), 'private')
        self.assertEqual((self.w / 'a').read_text(), 'edits')
        self.assertEqual(idx, self.git('ls-files', '-s'))
        self.assertEqual(self.git('stash', 'list'), b'')

    def test_tm_ignored_collision(self):
        self.write('collision', 'historical')
        self.git('add', 'collision')
        self.git('commit', '-qm', 'old')
        old = self.git('rev-parse', 'HEAD').decode().strip()
        self.git('rm', 'collision')
        self.write('.gitignore', 'collision\n')
        self.git('add', '.gitignore')
        self.git('commit', '-qm', 'present')
        self.write('collision', 'private')
        self.run_tm(f'if _dusky_git_checkout {old}; then exit 22; fi\n')
        self.assertEqual((self.w / 'collision').read_text(), 'private')

    def test_tm_edits_in_past_discarded_on_return(self):
        old = self.history()
        self.run_tm(f'_dusky_git_checkout {old} || exit 10\nprintf changed > a\n_dusky_git_return || exit 23\n')
        self.assertEqual((self.w / 'a').read_text(), 'present\n')

    def test_tm_relaunch_restores_stash(self):
        old = self.history()
        self.write('a', 'staged')
        self.git('add', 'a')
        self.write('a', 'unstaged')
        idx = self.git('ls-files', '-s')
        self.run_tm(f'_dusky_git_checkout {old} || exit 10\n')
        self.run_tm('_dusky_write phase detached\n_dusky_find_session_stash >/dev/null || exit 24\n_dusky_write stash stashed\n_dusky_git_return || exit 25\n')
        self.assertEqual((self.w / 'a').read_text(), 'unstaged')
        self.assertEqual(idx, self.git('ls-files', '-s'))
        self.assertEqual(self.git('stash', 'list'), b'')

    def test_manifest_scans_match_full_filter(self):
        names = ['dir/a.conf', 'dir/deep/b.conf', 'dir/b.txt', 'other/c.conf']
        for name in names:
            self.write(name, 'new')
        for patterns in [['dir/*.conf'], ['dir'], ['*.conf'], ['dir/**/b.conf']]:
            self.write('.manifest', '\n'.join(patterns))
            expected = [e for e in m.changed_entries() if m.matches_pathspec(e[0], patterns) or m.matches_pathspec(e[1], patterns)]
            self.assertEqual(set(m.scoped_entries()), set(expected))

    def test_tm_file_index_preserves_control_characters(self):
        names = ['tab\tfile', 'end\n', '[literal]', 'unicodé']
        for name in names:
            self.write(name, 'contents')
        self.git('add', '.')
        self.git('commit', '-qm', 'filenames')
        self.run_tm('_dusky_write drill_sha "$(_gr rev-parse HEAD)"\n_dusky_git_list_files >/dev/null\n')
        index = (self.root / 'session/state/files_index').read_bytes().split(b'\0')
        self.assertEqual(set(index) - {b''}, {os.fsencode(name) for name in names})

    def test_tm_owner_selftest_and_terminal(self):
        try:
            import pexpect
        except ImportError:
            self.skipTest('pexpect is required for the owner terminal test')
        self.history()
        runtime = self.root / 'runtime'
        runtime.mkdir()
        env = self.env | {'XDG_RUNTIME_DIR': str(runtime), 'DUSKY_TM_SANDBOX': '1', 'TERM': 'xterm-256color'}
        script = str(ROOT / 'time_machine/dusky_time_machine_tui.sh')
        result = subprocess.run(['bash', script, '--self-test'], env=env, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ALL PASSED', result.stdout)
        child = pexpect.spawn('bash', [script], env=env, encoding='utf-8', timeout=15, dimensions=(40,140))
        try:
            child.expect_exact('\x1b[?1049h')
            child.send('\x1b[1;1R')
            # Startup transforms replace the fixed title prompt with the
            # current phase; either frame proves the picker is ready.
            child.expect(r'Time Machine| :: present [0-9a-f]+')
            child.send('\x1b')
            child.expect(pexpect.EOF)
            child.close()
            self.assertEqual(child.exitstatus, 0)
        finally:
            child.close(force=True)


class PushTests(BareRepoCase):

    def remote(self):
        self.remote_dir = self.root / 'remote'
        subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(self.remote_dir)], env={k: v for k, v in self.env.items() if k not in ('GIT_DIR', 'GIT_WORK_TREE')}, check=True, stdout=subprocess.DEVNULL)
        self.git('remote', 'add', 'backup', str(self.remote_dir))
        self.git('push', '-u', 'backup', 'main:refs/heads/different')
        self.git('config', 'branch.main.merge', 'refs/heads/different')

    def advance(self, *, local=False):
        self.write('b' if local else 'a', 'local\n' if local else 'remote\n')
        self.git('add', 'b' if local else 'a')
        self.git('commit', '-qm', 'advance')

    def test_push_different_upstream(self):
        self.remote()
        self.advance(local=True)
        self.assertTrue(m.safe_push())
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), subprocess.check_output(['git', '--git-dir=' + str(self.remote_dir), 'rev-parse', 'different'], env=self.env).strip())

    def test_fast_forward(self):
        self.remote()
        old = self.git('rev-parse', 'HEAD').decode().strip()
        self.advance()
        new = self.git('rev-parse', 'HEAD')
        self.git('push', 'backup', 'main:different')
        self.git('reset', '--hard', old)
        with patch.object(m, 'ask_yesno', return_value=True):
            self.assertTrue(m.safe_push())
        self.assertEqual(self.git('rev-parse', 'HEAD'), new)
        self.assertEqual((self.w / 'a').read_text(), 'remote\n')

    def test_diverged_rebase(self):
        self.remote()
        old = self.git('rev-parse', 'HEAD').decode().strip()
        self.advance()
        self.git('push', 'backup', 'main:different')
        self.git('reset', '--hard', old)
        self.advance(local=True)
        with patch.object(m, 'ask', return_value='1'):
            self.assertTrue(m.safe_push())
        self.assertEqual((self.w / 'a').read_text(), 'remote\n')
        self.assertEqual((self.w / 'b').read_text(), 'local\n')

    def test_dirty_divergence_preserved(self):
        self.remote()
        old = self.git('rev-parse', 'HEAD').decode().strip()
        self.advance()
        self.git('push', 'backup', 'main:different')
        self.git('reset', '--hard', old)
        self.advance(local=True)
        self.write('a', 'private')
        before = self.git('rev-parse', 'HEAD')
        self.assertFalse(m.safe_push())
        self.assertEqual(before, self.git('rev-parse', 'HEAD'))
        self.assertEqual((self.w / 'a').read_text(), 'private')

class AdditionalTests(BareRepoCase):

    def test_discard_replacement_restores_head_after_approved_delete(self):
        self.write('.manifest', 'a\n')
        self.git('rm', '--cached', 'a')
        self.write('a', 'replacement')
        with patch.object(m, 'ask_yesno', return_value=True):
            m.discard_local_changes()
        self.assertEqual((self.w / 'a').read_text(), 'base\n')
        self.assertEqual(self.git('diff', 'HEAD', '--', 'a'), b'')

    def test_discard_preserves_unapproved_untracked_replacement(self):
        self.write('.manifest', 'a\n')
        self.git('rm', '--cached', 'a')
        self.write('a', 'replacement')
        before = self.git('status', '--porcelain=v1', '-z')
        with patch.object(m, 'ask_yesno', side_effect=[True, False]):
            m.discard_local_changes()
        self.assertEqual((self.w / 'a').read_text(), 'replacement')
        self.assertEqual(self.git('status', '--porcelain=v1', '-z'), before)

    def test_untrack_keeps_disk_file(self):
        self.git('rm', '--cached', 'a')
        self.commit([entry for entry in m.changed_entries() if entry[0] == 'a' and entry[2] == 'D '])
        self.assertEqual(self.git('ls-files', 'a'), b'')
        self.assertEqual((self.w / 'a').read_text(), 'base\n')

    def test_failing_commit_hook_preserves_index(self):
        hook = self.g / 'hooks/pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(448)
        self.write('a', 'new')
        self.write('b', 'other')
        self.git('add', 'b')
        before = self.git('rev-parse', 'HEAD')
        bindex = self.git('ls-files', '-s', 'b')
        self.commit([entry for entry in m.changed_entries() if entry[0] == 'a'])
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual(self.git('ls-files', '-s', 'b'), bindex)
        self.assertEqual(self.git('show', ':a'), b'new')

    def test_edited_commit_hook_updates_selected_index_only(self):
        hook = self.g / 'hooks/pre-commit'
        hook.write_text('#!/bin/sh\nprintf formatted > "$GIT_WORK_TREE/a"\ngit add -- a\n')
        hook.chmod(448)
        self.write('a', 'new')
        self.write('b', 'other')
        self.git('add', 'b')
        bindex = self.git('ls-files', '-s', 'b')
        self.commit([entry for entry in m.changed_entries() if entry[0] == 'a'])
        self.assertEqual(self.git('show', 'HEAD:a'), b'formatted')
        self.assertEqual(self.git('diff', '--cached', '--', 'a'), b'')
        self.assertEqual(self.git('ls-files', '-s', 'b'), bindex)

    def test_shared_lock_blocks_dispatch(self):
        import fcntl
        lock_dir = self.g / 'dusky-time-machine'
        lock_dir.mkdir()
        with (lock_dir / 'worktree.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            action = m.Action('test', 'Test', 0, False, lambda: self.fail('locked action executed'))
            with patch.dict(m.ACTION_MAP, {'test': action}):
                m.dispatch('test')

    def test_fzf_failure_is_reported(self):
        result = subprocess.CompletedProcess(['fzf'], 2, stdout=b'')
        with patch.object(m.subprocess, 'run', return_value=result):
            with self.assertRaises(RuntimeError):
                m.fzf_select(['a'])

class TerminalTests(unittest.TestCase):

    def setUp(self):
        try:
            import pexpect
        except ImportError:
            self.skipTest('pexpect is required for terminal tests')
        self.pexpect = pexpect
        self.env = os.environ | {'TERM': 'xterm-256color', 'FZF_DEFAULT_OPTS': '--height=10% --preview=echo BAD --bind=enter:abort', 'FZF_DEFAULT_OPTS_FILE': '/does/not/exist'}
        self.load = f"import runpy; m=runpy.run_path({str(ROOT / 'git_dusky.py')!r}); "

    def test_python_arrow_editing(self):
        p = self.pexpect.spawn(sys.executable, ['-B', '-c', self.load + "print('RESULT',repr(m['ask']('PROMPT> ')))"], env=self.env, encoding='utf-8', timeout=10)
        try:
            p.expect_exact('PROMPT> ')
            p.send('abc\x1b[DX\r')
            p.expect_exact("RESULT 'abXc'")
            p.expect(self.pexpect.EOF)
        finally:
            p.close(force=True)

    def test_fzf_fullscreen_ignores_global_defaults(self):
        p = self.pexpect.spawn(sys.executable, ['-B', '-c', self.load + "print('RESULT',repr(m['fzf_select'](['alpha','beta'],multi=True)))"], env=self.env, encoding='utf-8', timeout=10, dimensions=(40, 120))
        try:
            p.expect_exact('\x1b[?1049h')
            p.send('\x1b[1;1R')
            p.expect('alpha')
            p.send('\r')
            p.expect_exact("RESULT ['alpha']")
            p.expect(self.pexpect.EOF)
        finally:
            p.close(force=True)
class ShellIntegrationTests(unittest.TestCase):
    def setUp(self):
        try:
            import pexpect
        except ImportError:
            self.skipTest('pexpect is required for shell integration tests')
        self.pexpect = pexpect
        self.config = Path.home() / '.config/zshrc/git'
        if not self.config.is_file():
            self.skipTest('installed Zsh git module is unavailable')
        self.temp = tempfile.TemporaryDirectory(prefix='dusky-shell-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / 'fixture').write_text('untouched')
        self.env = os.environ | {'HOME': str(self.home), 'TERM': 'xterm-256color'}
        self.env.pop('TMUX_PANE', None)

    def test_shell_commit_prompt_arrow_editing(self):
        # Exercise the real shell helper, with Git stubbed to prevent writes.
        script = self.home / 'check.zsh'
        script.write_text('source "$1"\nfunction git_dusky() {\n case "$1" in\n diff) [[ $3 == --quiet ]] && return 1 ;;\n commit) print -r -- "MESSAGE=$3" ;;\n branch) print main ;;\n esac\n return 0\n}\ngit_dusky_push "$HOME/fixture"\n')
        child = self.pexpect.spawn('zsh', ['-f', str(script), str(self.config)], env=self.env, encoding='utf-8', timeout=10)
        try:
            child.expect_exact("Commit message for 'fixture': ")
            child.send('abc\x1b[DX\r')
            child.expect_exact('MESSAGE=abXc (fixture)')
            child.expect(self.pexpect.EOF)
            child.close()
            self.assertEqual(child.exitstatus, 0)
        finally:
            child.close(force=True)

    def test_shell_ctrl_t_fullscreen(self):
        env = self.env | {'FZF_CTRL_T_COMMAND': 'printf "fixture\\n"', 'FZF_CTRL_T_OPTS': '--height=10%', 'FZF_DEFAULT_OPTS': '--height=20%'}
        child = self.pexpect.spawn('zsh', ['-f'], env=env, encoding='utf-8', timeout=12, dimensions=(40,120))
        try:
            child.sendline('PROMPT="READY> "; cd -- "$HOME"')
            child.expect_exact('READY> ')
            import shlex
            quoted = shlex.quote(str(self.config))
            child.sendline(f'source {quoted}; source {quoted}; source <(fzf --zsh)')
            child.expect_exact('READY> ')
            child.send('\x14')
            child.expect_exact('\x1b[?1049h')
            child.send('\x1b[1;1R')
            child.expect('fixture')
            child.send('\r')
            child.expect_exact('READY> fixture')
            child.send('\x03')
            child.sendline('print -r -- "OPTS=${FZF_CTRL_T_OPTS}"')
            child.expect_exact('OPTS=--height=10% --no-height\r\n')
        finally:
            child.close(force=True)

if __name__ == '__main__':
    unittest.main(verbosity=2)
