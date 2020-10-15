# Copyright (c) 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Miscellaneous Python 2-3 compatibility functions.

Seven is an extension to the compatibility layer six.
It contains utilities that ease migration from Python 2
to Python 3, but aren't present in the six library.
"""

import six


def exec_file(filename, globals_, locals_):
    """exec_file compiles and runs a file with globals and locals.

    exec_file does not exactly mimic all the edge cases in Python 2's
    execfile function. Rather, it does only what is necessary to execute
    control files in autotest and prevent compiler-wide settings like
    'from __future__ import ...' from spilling into control files that
    have not yet been made Python 3-compatible.

    Arguments:
        filename:   path to a file
        globals_:    dictionary of globals
        locals_:     dictionary of locals

    Returns:
        None (output of six.exec_)
    """
    with open(filename, "rb") as fh:
        code_obj = compile(
                fh.read(),
                filename,
                mode="exec",
                flags=0,
                dont_inherit=1,
        )
    return six.exec_(code_obj, globals_, locals_)
