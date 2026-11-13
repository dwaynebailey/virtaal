#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Making translated documents in a child process, so a bundle's large
documents don't hold up the window while saving. No GTK here.

    python -m virtaal.support.docregen FOLDER

makes the document of each job FOLDER/jobs.json lists, and notes in
FOLDER/results.json which it made."""

import json
import os
import shutil
import subprocess
import sys
import tempfile

JOBS = 'jobs.json'
RESULTS = 'results.json'


def run(folder):
    """Make the documents of the jobs in C{folder}."""
    from virtaal.support import converters

    with open(os.path.join(folder, JOBS), encoding='utf-8') as f:
        jobs = json.load(f)
    results = []
    for job in jobs:
        converter = converters.document_converter_by_id(job['converter'])
        try:
            with open(os.path.join(folder, job['trans']), 'rb') as f:
                trans = f.read()
            with open(os.path.join(folder, job['source']), 'rb') as f:
                source = f.read()
            target = converter.back(trans, source)
            with open(os.path.join(folder, job['out']), 'wb') as f:
                f.write(target)
            results.append(None)
        except Exception as exc:
            results.append(str(exc) or repr(exc))
    with open(os.path.join(folder, RESULTS), 'w', encoding='utf-8') as f:
        json.dump(results, f)


class Regeneration:
    """The documents of a bundle's translations being made in a child
    process. L{poll()} until it gives the results."""

    def __init__(self, jobs, command=None):
        """C{jobs}: [(entry, converter id, trans bytes, source bytes)],
        as DocumentBundle.regeneration_jobs() gives them."""
        from virtaal.support import documents, helperprocess

        self.folder = tempfile.mkdtemp(prefix='virtaal-regen-')
        self.entries = []
        listed = []
        for number, (entry, converter_id, trans, source) in enumerate(jobs):
            names = {kind: '%d.%s' % (number, kind) for kind in ('trans', 'source', 'out')}
            for kind, data in (('trans', trans), ('source', source)):
                with open(os.path.join(self.folder, names[kind]), 'wb') as f:
                    f.write(data)
            listed.append(dict(names, converter=converter_id))
            self.entries.append((entry, documents.sha256(trans)))
        with open(os.path.join(self.folder, JOBS), 'w', encoding='utf-8') as f:
            json.dump(listed, f)
        command = command or helperprocess.command('virtaal.support.docregen')
        self.process = subprocess.Popen(
            [*command, self.folder],
            env=helperprocess.env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def poll(self):
        """None while the child works; then ([(entry, target bytes, hash
        of the trans file it was made from)], {entry index: error})."""
        if self.process.poll() is None:
            return None
        try:
            with open(os.path.join(self.folder, RESULTS), encoding='utf-8') as f:
                results = json.load(f)
        except (OSError, ValueError):
            results = ['The helper process stopped.'] * len(self.entries)
        made, failed = [], {}
        for number, ((entry, made_from), error) in enumerate(
            zip(self.entries, results, strict=False)
        ):
            if error is not None:
                failed[number] = error
                continue
            with open(os.path.join(self.folder, '%d.out' % number), 'rb') as f:
                made.append((entry, f.read(), made_from))
        self.close()
        return made, failed

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        shutil.rmtree(self.folder, ignore_errors=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    run(argv[0])


if __name__ == '__main__':
    main()
