#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Recovery across real Virtaal processes: each launch is a fresh
process with its own display connection, killed or stopped the way a
crash or a shutdown would end it."""

import json
import os
import signal
import subprocess
import sys
import textwrap

import pytest

pytestmark = [
    pytest.mark.skipif(
        not sys.platform.startswith('linux') or not os.environ.get('DISPLAY'),
        reason='Linux with an X display',
    ),
    # Each launch takes a few seconds.
    pytest.mark.timeout(240),
]

PO = '''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
"Language: af\\n"

msgid "Open"
msgstr ""

msgid "Close"
msgstr "Maak toe"

msgid "Save the file"
msgstr "Stoor die lêer"

msgid "Quit"
msgstr ""
'''

# Runs Virtaal as bin/virtaal does, then follows the steps in $DRIVE
# and writes what the window shows to $REPORT.
DRIVER = textwrap.dedent(
    r'''
    import json, os, signal, sys, time
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import GLib, Gtk
    from virtaal.common import pan_app

    for name in ('tm', 'terminology', 'lookup', 'spellchecker', 'autocorrector'):
        pan_app.settings.plugin_state[name] = 'disabled'
    pan_app.settings.language['sourcelang'] = 'en'
    pan_app.settings.language['targetlang'] = 'af'
    pan_app.settings.translator['name'] = 'Tester'
    pan_app.settings.translator['email'] = 'tester@example.org'

    from virtaal.main import Virtaal

    steps = json.loads(os.environ['DRIVE'])
    app = Virtaal(sys.argv[1] if len(sys.argv) > 1 else '')
    mc = app.main_controller
    mc.view.main_window.resize(900, 600)

    def frames(n):
        for _ in range(n):
            yield

    def wait(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            yield

    def notices():
        found = []
        for child in mc.view.gui.get_object('vbox_main').get_children():
            if isinstance(child, Gtk.InfoBar) and child.get_visible():
                labels = [w.get_text() for w in child.get_content_area().get_children()]
                buttons = [b.get_label() for b in child.get_action_area().get_children()]
                found.append({'text': ' '.join(labels), 'buttons': buttons, 'bar': child})
        return found

    def state():
        sc = mc.store_controller
        out = {'store': None, 'notices': [
            {k: v for k, v in n.items() if k != 'bar'} for n in notices()
        ]}
        if sc.store is not None:
            view = mc.unit_controller.view
            n = view.focused_target_n or 0
            out.update(
                store=sc.store.get_filename(),
                index=sc.cursor.index,
                modified=sc.is_modified(),
                targets=[str(sc.store[i].target) for i in range(len(sc.store))],
                target_n=n,
                offset=view.targets[n].get_cursor_position(),
                editor_text=view.targets[n].get_text(),
                editor_mapped=view.targets[n].get_mapped(),
                editor_height=view.targets[n].get_allocated_height(),
            )
        return out

    def report(name):
        with open(os.environ['REPORT'] + '.' + name, 'w', encoding='utf-8') as f:
            json.dump(state(), f)

    def driver():
        # Settled: start-up's deferred jobs, plug-ins included, are done.
        end = time.monotonic() + 60
        while (app.defer._todo or mc.plugin_controller is None) and time.monotonic() < end:
            yield
        yield from wait(1)
        for step in steps:
            kind = step[0]
            if kind == 'report':
                report(step[1])
            elif kind == 'goto':
                mc.store_controller.cursor.force_index(step[1])
                yield from frames(20)
            elif kind == 'offset':
                mc.unit_controller.view.targets[0].place_cursor(step[1])
                yield from frames(5)
            elif kind == 'type':
                buffer = mc.unit_controller.view.targets[0].buffer
                buffer.insert_interactive_at_cursor(step[1], -1, True)
                yield from frames(5)
            elif kind == 'wait':
                yield from wait(step[1])
            elif kind == 'press':
                [bar] = [n['bar'] for n in notices() if step[1] in n['buttons']]
                index = [n['buttons'] for n in notices() if n['bar'] is bar][0].index(step[1])
                bar.get_action_area().get_children()[index].clicked()
                yield from frames(20)
            elif kind == 'kill':
                os.kill(os.getpid(), signal.SIGKILL)
            elif kind == 'term':
                os.kill(os.getpid(), signal.SIGTERM)
                yield from wait(30)
            elif kind == 'quit':
                mc.quit()
                yield from wait(30)

    gen = driver()
    GLib.timeout_add(50, lambda: next(gen, False) is not False)
    GLib.timeout_add_seconds(90, lambda: os._exit(3))
    app.run()
    os._exit(0)
    '''
)


class _Launcher:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.home = tmp_path / 'home'
        self.home.mkdir()
        self.state = tmp_path / 'state'
        self.driver = tmp_path / 'driver.py'
        self.driver.write_text(DRIVER, encoding='utf-8')
        self.po = tmp_path / 'files' / 'open.po'
        self.po.parent.mkdir()
        self.po.write_text(PO, encoding='utf-8')
        self.report = str(tmp_path / 'report')
        self.root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def run(self, steps, *args):
        env = dict(
            os.environ,
            HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / '.config'),
            XDG_STATE_HOME=str(self.state),
            PYTHONPATH=self.root,
            DRIVE=json.dumps(steps),
            REPORT=self.report,
        )
        proc = subprocess.run(
            [sys.executable, str(self.driver), *args],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=150,
        )
        return proc

    def read(self, name):
        with open(self.report + '.' + name, encoding='utf-8') as f:
            return json.load(f)

    def slots(self):
        root = self.state / 'virtaal' / 'recovery'
        if not root.is_dir():
            return []
        return sorted(p for p in root.iterdir() if '.' not in p.name)

    def session(self):
        [slot] = self.slots()
        return json.loads((slot / 'session.json').read_text(encoding='utf-8'))


@pytest.fixture
def launcher(tmp_path):
    return _Launcher(tmp_path)


def _killed(proc):
    assert proc.returncode == -signal.SIGKILL, proc.stderr[-2000:]


def test_a_crash_with_nothing_unsaved_resumes_silently_at_the_unit_and_offset(launcher):
    po = str(launcher.po)
    _killed(launcher.run([['goto', 2], ['offset', 5], ['wait', 2.5], ['kill']], po))

    proc = launcher.run([['report', 'resumed'], ['quit']])

    resumed = launcher.read('resumed')
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert resumed['store'] == po
    assert (resumed['index'], resumed['target_n'], resumed['offset']) == (2, 0, 5)
    assert resumed['editor_mapped'], resumed
    assert resumed['notices'] == []
    assert not resumed['modified']
    # A clean quit leaves nothing to resume.
    assert launcher.slots() == []


def test_a_crash_with_edits_asks_and_recover_reapplies_them(launcher):
    po = str(launcher.po)
    steps = [['goto', 0], ['type', 'Oop'], ['goto', 3], ['type', 'Maak'], ['wait', 2.5], ['kill']]
    _killed(launcher.run(steps, po))

    _killed(
        launcher.run(
            [['report', 'asked'], ['press', '_Recover'], ['report', 'recovered'], ['kill']]
        )
    )

    asked = launcher.read('asked')
    [notice] = asked['notices']
    assert 'Virtaal closed unexpectedly. 2 unsaved units from' in notice['text']
    assert sorted(notice['buttons']) == ['_Discard', '_Recover']
    assert asked['targets'] == ['', 'Maak toe', 'Stoor die lêer', '']
    assert asked['index'] == 3
    recovered = launcher.read('recovered')
    assert recovered['targets'] == ['Oop', 'Maak toe', 'Stoor die lêer', 'Maak']
    assert recovered['editor_text'] == 'Maak'
    assert recovered['modified']
    assert recovered['notices'] == []


def test_discard_drops_the_edits_and_keeps_the_position(launcher):
    po = str(launcher.po)
    _killed(launcher.run([['goto', 1], ['type', ' nou'], ['goto', 2], ['wait', 2.5], ['kill']], po))

    _killed(launcher.run([['press', '_Discard'], ['wait', 2], ['kill']]))
    launcher.run([['report', 'again'], ['quit']])

    again = launcher.read('again')
    assert again['targets'][1] == 'Maak toe'
    assert again['index'] == 2
    assert again['notices'] == []


def test_two_crashes_while_resuming_show_the_welcome_screen(launcher):
    po = str(launcher.po)
    _killed(launcher.run([['goto', 2], ['wait', 2.5], ['kill']], po))
    # Each crash comes before the resumed session has been stable for 5s.
    _killed(launcher.run([['kill']]))
    _killed(launcher.run([['kill']]))

    _killed(
        launcher.run(
            [['report', 'guarded'], ['press', '_Reopen'], ['report', 'reopened'], ['kill']]
        )
    )

    guarded = launcher.read('guarded')
    assert guarded['store'] is None
    [notice] = guarded['notices']
    assert notice['text'] == "Virtaal closed unexpectedly while reopening '%s'." % po
    reopened = launcher.read('reopened')
    assert (reopened['store'], reopened['index']) == (po, 2)


def test_sigterm_quits_without_a_dialog_and_resumes_with_the_edits(launcher):
    po = str(launcher.po)
    steps = [['goto', 0], ['type', 'Oop'], ['goto', 1], ['offset', 4], ['type', ' nou'], ['term']]

    proc = launcher.run(steps, po)

    # A dialog would have held the process until the watchdog's exit 3.
    assert proc.returncode == 0, proc.stderr[-2000:]
    session = launcher.session()
    assert session['end_reason'] == 'signal'
    assert session['documents']['0']['draft']['fields']['targets'] == ['Maak nou toe']

    launcher.run([['report', 'resumed'], ['kill']])

    resumed = launcher.read('resumed')
    assert resumed['targets'][:2] == ['Oop', 'Maak nou toe']
    assert (resumed['index'], resumed['offset']) == (1, 8)
    assert [n['text'] for n in resumed['notices']] == [
        'Restored 2 unsaved units from your last session.'
    ]
    assert resumed['modified']
