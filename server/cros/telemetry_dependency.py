# Lint as: python3
# -*- coding: utf-8 -*-
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Decides whether a client autotest test needs Telemetry on the DUT.

Client tests that use Telemetry import it at module scope, usually indirectly
via client/common_lib/cros/chrome.py. That means they fail during import, before
any test code runs, so the framework has to be present on the DUT before the
client test process is even launched. The server side therefore has to know, in
advance, whether a given test is going to need it.

Tests declare this explicitly:

    REQUIRE_TELEMETRY = True

Note this is deliberately *not* expressed through the control file's
DEPENDENCIES field. Entries there are matched against DUT labels for scheduling
-- see suite._create_job_deps and test_runner_utils.deps_satisfied -- so
declaring a 'telemetry' dependency would restrict the test to DUTs carrying a
'telemetry' label, and it would silently stop being scheduled. Telemetry is a
payload we install on demand, not a property of the hardware.

The declaration is read from either the test's control file(s) or the test's
Python module. Supporting both is necessary rather than merely convenient:

  - Some client tests have no control file at all (login_LoginSuccess,
    stub_IdleSuspend). They exist only to be invoked as
    client_at.run_test('login_LoginSuccess') from a server-side test, so the
    module is the only place the declaration can live.
  - Some tests have many control files -- power_LoadTest has 24 variants.
    Requiring every variant to be annotated would be tedious and easy to get
    wrong, so a declaration in any one of them, or in the module, counts for the
    whole test.

Both are read statically: control files via control_data (which uses ast.parse),
modules via ast directly. Nothing is imported or executed.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import ast
import logging
import os

import common
from autotest_lib.client.common_lib import control_data
from autotest_lib.server.cros import telemetry_deploy

# Root of the autotest checkout, derived from this file's location
# (<root>/server/cros/telemetry_dependency.py).
_AUTOTEST_ROOT = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Directories a client test may live in, relative to the autotest root.
_CLIENT_TEST_DIRS = (
        os.path.join('client', 'site_tests'),
        os.path.join('client', 'tests'),
)

# The control file variable, and the equivalent module-level constant.
DECLARATION = 'REQUIRE_TELEMETRY'

# Variable injected at the top of server/control_segments/client_trampoline.
_TRAMPOLINE_VAR = 'trampoline_testname'

# Memoized answers, keyed by normalized test name. A single server job can
# launch the same client test repeatedly.
_cache = {}


def _normalize_test_name(test_name):
    """Normalizes a client test URL or name to a relative directory path.

    Mirrors how the client harness resolves test names:
      - client/bin/job.py:stage_control_file strips a '.<tag>' suffix
        (e.g. 'camera_HAL3.jea' -> 'camera_HAL3').
      - client/common_lib/test.py:runtest converts ':' into '/'.

    @param test_name: Raw test name or URL passed to job.run_test.

    @returns Normalized relative path string, or '' if invalid.
    """
    if not isinstance(test_name, str):
        return ''
    base_name, _, _ = test_name.strip().partition('.')
    return base_name.replace(':', '/')


def _test_dir(test_name):
    """Locates a client test's source directory.

    @param test_name: Name of the client test.

    @returns Absolute path to the test directory, or None if not found.
    """
    normalized = _normalize_test_name(test_name)
    if not normalized or os.path.isabs(normalized):
        return None

    for parent in _CLIENT_TEST_DIRS:
        base = os.path.realpath(os.path.join(_AUTOTEST_ROOT, parent))
        candidate = os.path.realpath(os.path.join(base, normalized))
        try:
            if (os.path.commonpath([base, candidate]) != base
                        or candidate == base):
                continue
        except ValueError:
            continue
        if os.path.isdir(candidate):
            return candidate
    return None


def _string_literal(node):
    """Extracts a string literal from an AST node, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Str) and isinstance(node.s, str):
        return node.s
    return None


def _assignment_targets_and_value(node):
    """Returns (targets, value) for a top-level assignment node."""
    if isinstance(node, ast.Assign):
        return node.targets, node.value
    if isinstance(node, ast.AnnAssign):
        return [node.target], node.value
    return (), None


def _parse_tree(source, filename='<unknown>'):
    """Parses Python source into an AST, or returns None on syntax error."""
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, TypeError) as e:
        logging.debug('Could not parse %s: %s', filename, e)
        return None


def _declared_in_module_tree(tree):
    """Returns whether an AST sets REQUIRE_TELEMETRY = True at module scope."""
    if tree is None:
        return False

    declared = False
    for node in tree.body:
        targets, value = _assignment_targets_and_value(node)
        for target in targets:
            if isinstance(target, ast.Name) and target.id == DECLARATION:
                declared = (value is not None
                            and isinstance(value, ast.Constant)
                            and value.value is True)

    return declared


def _declared_in_source(source, filename='<unknown>'):
    """Returns whether Python source code declares that Telemetry is required.

    Only module-level assignments count, so a same-named attribute set inside a
    class or function is ignored. All top-level statements are inspected in
    order so a later assignment overrides an earlier one, matching
    control_data.py.

    @param source: Python or control-file source text.
    @param filename: Name used in debug logs on a parse failure.

    @returns True if the source sets REQUIRE_TELEMETRY = True at module scope.
    """
    return _declared_in_module_tree(_parse_tree(source, filename=filename))


def _declared_in_control_tree(tree, filename='<control>'):
    """Returns whether a parsed control-file AST declares REQUIRE_TELEMETRY.

    Calls control_data.finish_parse() directly on the already-parsed AST so the
    source is parsed only once and control_data.parse_control_string()'s
    line-by-line logging.error dump on syntax errors is avoided. When
    finish_parse() succeeds its answer is authoritative (including stepN()
    overrides and string booleans); only synthesized snippets that omit
    required control headers fall back to _declared_in_module_tree().
    """
    if tree is None:
        return False

    try:
        control = control_data.finish_parse(tree,
                                            path=filename,
                                            raise_warnings=True)
        return bool(getattr(control, 'require_telemetry', False))
    except Exception as e:
        logging.debug('Could not parse %s via control_data: %s', filename, e)
        return _declared_in_module_tree(tree)


def _declared_in_control_string(control_text, filename='<control>'):
    """Returns whether control-file source text declares REQUIRE_TELEMETRY.

    @param control_text: Contents of a client control file.
    @param filename: Name used in debug logs on a parse failure.

    @returns True if the control text sets REQUIRE_TELEMETRY.
    """
    return _declared_in_control_tree(_parse_tree(control_text,
                                                 filename=filename),
                                     filename=filename)


def _declared_in_control_file(path):
    """Returns whether a control file declares that Telemetry is required.

    @param path: Absolute path to a control file.

    @returns True if the control file sets REQUIRE_TELEMETRY.
    """
    try:
        with open(path, 'r', errors='replace') as f:
            return _declared_in_control_string(f.read(), filename=path)
    except (IOError, OSError) as e:
        logging.debug('Could not read %s: %s', path, e)
        return False


def _declared_in_module(path):
    """Returns whether a Python module declares that Telemetry is required.

    @param path: Absolute path to a .py file.

    @returns True if the module sets REQUIRE_TELEMETRY = True at module scope.
    """
    try:
        with open(path, 'r', errors='replace') as f:
            return _declared_in_source(f.read(), filename=path)
    except (IOError, OSError) as e:
        logging.debug('Could not read %s: %s', path, e)
        return False


def _tests_in_tree(tree):
    """Extracts client test names invoked by a parsed control-file AST.

    Handles direct job.run_test('<name>', ...) / job.run_test_detail(...) calls
    (used by standalone control files and by Autotest.run_timed_test), the
    trampoline_testname = '<name>' assignment injected at the top of
    server/control_segments/client_trampoline, and the top-level NAME variable
    for control files that invoke job.run_test(url=NAME.split('.')[0], ...).
    """
    if tree is None:
        return []

    names = []
    for node in tree.body:
        targets, value = _assignment_targets_and_value(node)
        for target in targets:
            if (isinstance(target, ast.Name)
                        and target.id in (_TRAMPOLINE_VAR, 'NAME')):
                literal = _string_literal(value)
                if literal:
                    names.append(literal)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute)
                and node.func.attr in ('run_test', 'run_test_detail')):
            continue
        literal = None
        if node.args:
            literal = _string_literal(node.args[0])
        if literal is None:
            for kw in node.keywords:
                if kw.arg == 'url':
                    literal = _string_literal(kw.value)
                    break
        if literal:
            names.append(literal)

    return names


def _tests_in_control_string(control_text, filename='<control>'):
    """Extracts client test names invoked by control-file source text.

    @param control_text: Contents of a client control file.
    @param filename: Name used in debug logs on a parse failure.

    @returns List of test names in the order they appear.
    """
    return _tests_in_tree(_parse_tree(control_text, filename=filename))


def _tests_in_control_file(path):
    """Extracts client test names invoked by a control file on disk.

    @param path: Absolute path to a control file.

    @returns List of test names in the order they appear.
    """
    try:
        with open(path, 'r', errors='replace') as f:
            return _tests_in_control_string(f.read(), filename=path)
    except (IOError, OSError) as e:
        logging.debug('Could not read %s: %s', path, e)
        return []


def client_test_needs_telemetry(test_name):
    """Returns whether a client test has declared that it needs Telemetry.

    @param test_name: Name of the client test, as passed to run_test.

    @returns True if Telemetry must be present on the DUT for this test.
    """
    key = _normalize_test_name(test_name)
    if not key:
        return False
    if key not in _cache:
        _cache[key] = _read_declaration(key)
    return _cache[key]


def _read_declaration(test_name):
    """Reads the Telemetry declaration for a client test.

    @param test_name: Normalized name of the client test.

    @returns True if any of the test's control files or modules declares it.
    """
    test_dir = _test_dir(test_name)
    if test_dir is None:
        # Not a client test we can see from here; it may be a server test, or
        # the sources may not be present.
        logging.debug('No client test directory for %s.', test_name)
        return False

    for entry in sorted(os.listdir(test_dir)):
        path = os.path.join(test_dir, entry)
        if not os.path.isfile(path):
            continue

        if entry == 'control' or entry.startswith('control.'):
            declared = _declared_in_control_file(path)
        elif entry.endswith('.py'):
            declared = _declared_in_module(path)
        else:
            continue

        if declared:
            logging.info('%s declares %s (in %s).', test_name, DECLARATION,
                         entry)
            return True

    return False


def control_string_needs_telemetry(control_text, filename='<control>'):
    """Returns whether control-file text or any test it runs needs Telemetry.

    @param control_text: Contents of the client control file.
    @param filename: Name used in log messages.

    @returns True if Telemetry must be present on the DUT for this control file.
    """
    tree = _parse_tree(control_text, filename=filename)
    if tree is None:
        return False

    if _declared_in_control_tree(tree, filename=filename):
        logging.info('Control file %s declares %s.', filename, DECLARATION)
        return True

    for test_name in _tests_in_tree(tree):
        if client_test_needs_telemetry(test_name):
            return True

    return False


def control_file_needs_telemetry(path):
    """Returns whether a client control file or any test it runs needs Telemetry.

    @param path: Path to the client control file about to be sent to the DUT.

    @returns True if Telemetry must be present on the DUT for this control file.
    """
    try:
        with open(path, 'r', errors='replace') as f:
            return control_string_needs_telemetry(f.read(), filename=path)
    except (IOError, OSError) as e:
        logging.debug('Could not read %s: %s', path, e)
        return False


def _has_host_info_build(host):
    """Returns whether host_info_store already records a build for host."""
    try:
        return bool(host.host_info_store.get().build)
    except Exception:
        return False


def _release_builder_path(host):
    """Reads CHROMEOS_RELEASE_BUILDER_PATH from /etc/lsb-release on the DUT.

    Used in CFT (tauto_driver.go), where --host_labels is populated from
    DutTopology and omits cros-version: (b/328620954), leaving
    host.host_info_store.get().build as None.

    @param host: Autotest host object for the DUT.

    @returns The build string, or None if unavailable.
    """
    get_release_builder_path = getattr(host, 'get_release_builder_path', None)
    if not callable(get_release_builder_path):
        return None
    try:
        builder_path = get_release_builder_path()
    except Exception as e:
        logging.warning('Could not read release builder path from %s: %s',
                        getattr(host, 'hostname', host), e)
        return None
    return (builder_path
            if isinstance(builder_path, str) and builder_path else None)


def _ensure_on_dut(host):
    """Deploys Telemetry to the DUT, falling back to /etc/lsb-release in CFT.

    Calls ensure_telemetry_on_dut(host) first so the common fast path
    (Telemetry already present on the DUT, or cros-version: present in
    host_info_store) incurs no extra SSH round-trip to read /etc/lsb-release.
    Only queries host.get_release_builder_path() if ensure_telemetry_on_dut
    fails when host_info_store has no build.

    @param host: Autotest host object for the DUT.

    @returns True if Telemetry was deployed by this call.

    @raises telemetry_deploy.TelemetryDeployError: If Telemetry is needed and
            cannot be deployed.
    """
    try:
        return telemetry_deploy.ensure_telemetry_on_dut(host)
    except telemetry_deploy.TelemetryDeployError:
        if _has_host_info_build(host):
            raise
        build = _release_builder_path(host)
        if not build:
            raise
        return telemetry_deploy.ensure_telemetry_on_dut(host, build=build)


def ensure_for_client_test(host, test_name):
    """Deploys Telemetry to the DUT if the given client test declares it.

    @param host: Autotest host object for the DUT.
    @param test_name: Name of the client test about to be launched.

    @returns True if Telemetry was deployed by this call.

    @raises telemetry_deploy.TelemetryDeployError: If the test requires
            Telemetry and deployment to the DUT fails.
    """
    try:
        needs_telemetry = client_test_needs_telemetry(test_name)
    except Exception as e:
        logging.warning('Could not check Telemetry dependency for %s: %s',
                        test_name, e)
        return False

    if not needs_telemetry:
        return False

    return _ensure_on_dut(host)


def ensure_for_client_control_string(host, control_text, filename='<control>'):
    """Deploys Telemetry to the DUT if control-file contents require it.

    Covers both standalone client tests executed via their control file
    (client_wrapper, client_trampoline, WrapperTestRunner) and client tests
    synthesized by Autotest.run_timed_test().

    @param host: Autotest host object for the DUT.
    @param control_text: Contents of the client control file being executed.
    @param filename: Name used in log messages.

    @returns True if Telemetry was deployed by this call.

    @raises telemetry_deploy.TelemetryDeployError: If the control file requires
            Telemetry and deployment to the DUT fails.
    """
    try:
        needs_telemetry = control_string_needs_telemetry(control_text,
                                                         filename=filename)
    except Exception as e:
        logging.warning('Could not check Telemetry dependency for %s: %s',
                        filename, e)
        return False

    if not needs_telemetry:
        return False

    return _ensure_on_dut(host)


def ensure_for_client_control(host, path):
    """Deploys Telemetry to the DUT if a client control file requires it.

    @param host: Autotest host object for the DUT.
    @param path: Local path to the client control file being executed.

    @returns True if Telemetry was deployed by this call.

    @raises telemetry_deploy.TelemetryDeployError: If the control file requires
            Telemetry and deployment to the DUT fails.
    """
    try:
        needs_telemetry = control_file_needs_telemetry(path)
    except Exception as e:
        logging.warning('Could not check Telemetry dependency for %s: %s',
                        path, e)
        return False

    if not needs_telemetry:
        return False

    return _ensure_on_dut(host)
