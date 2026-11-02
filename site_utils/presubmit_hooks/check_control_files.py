#!/usr/bin/env vpython3
# Copyright 2013 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# [VPYTHON:BEGIN]
# python_version: "3.11"
#
# wheel: <
#   name: "infra/python/wheels/six-py2_py3"
#   version: "version:1.16.0"
# >
# [VPYTHON:END]
"""
Check an autotest control file for required variables.

This wrapper is invoked through autotest's PRESUBMIT.cfg for every commit
that edits a control file.
"""


import argparse
import fnmatch
import os
from pathlib import Path
import re
import site
import sys

import common
from autotest_lib.client.common_lib import control_data
from autotest_lib.server.cros.dynamic_suite import reporting_utils


def find_checkout() -> Path:
    """Find the base path of the chromiumos checkout."""
    for path in Path(__file__).resolve().parent.parents:
        if (path / ".repo").is_dir():
            return path
    raise OSError("Unable to locate chromiumos checkout.")


site.addsitedir(find_checkout())

from chromite.lib import build_query

DEPENDENCY_ARC = 'arc'
SUITES_NEED_RETRY = set(['bvt-cq', 'bvt-inline'])
TESTS_NEED_ARC = 'cheets_'
BVT_ATTRS = set(['suite:smoke', 'suite:bvt-inline', 'suite:bvt-cq'])
TAST_PSA_URL = (
    'https://groups.google.com/a/chromium.org/d/topic/chromium-os-dev'
    '/zH1nO7OjJ2M/discussion')


class ControlFileCheckerError(Exception):
    """Raised when a necessary condition of this checker isn't satisfied."""


def GetAutotestTestPackages():
    """
    Return a list of ebuilds which should be checked for test existance.

    @return autotest packages in overlay repository.
    """
    return (build_query.Query(build_query.Ebuild).filter(
            lambda ebuild: ebuild.package_info.atom.startswith(
                    "chromeos-base/autotest-")).all())


def GetUseFlags():
    """Get the set of all use flags from autotest packages.

    @returns: useflags
    """
    useflags = set()
    for ebuild in GetAutotestTestPackages():
        useflags.update(ebuild.iuse)
    return useflags


def CheckSuites(ctrl_data, test_name, useflags):
    """
    Check that any test in a SUITE is also in an ebuild.

    Throws a ControlFileCheckerError if a test within a SUITE
    does not appear in an ebuild. For purposes of this check,
    the psuedo-suite "manual" does not require a test to be
    in an ebuild.

    @param ctrl_data: The control_data object for a test.
    @param test_name: A string with the name of the test.
    @param useflags: Set of all use flags from autotest packages.

    @returns: None
    """
    if (hasattr(ctrl_data, 'suite') and ctrl_data.suite and
        ctrl_data.suite != 'manual'):
        for flag in useflags:
            if flag == 'tests_%s' % test_name:
                return
        raise ControlFileCheckerError(
                'No ebuild entry for %s. To fix, please do the following: 1. '
                'Add your new test to one of the ebuilds referenced by '
                'autotest-all. 2. cros_workon --board=<board> start '
                '<your_ebuild>. 3. emerge-<board> <your_ebuild>' % test_name)


def CheckValidAttr(ctrl_data, attr_allowlist, bvt_allowlist, test_name):
    """
    Check whether ATTRIBUTES are in the allowlist.

    Throw a ControlFileCheckerError if tags in ATTRIBUTES don't exist in the
    allowlist.

    @param ctrl_data: The control_data object for a test.
    @param attr_allowlist: allowlist set parsed from the attribute_allowlist.
    @param bvt_allowlist: allowlist set parsed from the bvt_allowlist.
    @param test_name: A string with the name of the test.

    @returns: None
    """
    if not (attr_allowlist >= ctrl_data.attributes):
        attribute_diff = ctrl_data.attributes - attr_allowlist
        raise ControlFileCheckerError(
                'Attribute(s): %s not in the allowlist in control file for test '
                'named %s. If this is a new attribute, please add it into '
                'AUTOTEST_DIR/site_utils/attribute_allowlist.txt file' %
                (attribute_diff, test_name))
    if ctrl_data.attributes & BVT_ATTRS:
        for pattern in bvt_allowlist:
            if fnmatch.fnmatch(test_name, pattern):
                break
        else:
            raise ControlFileCheckerError(
                    '%s not in the BVT allowlist. New BVT tests should be written '
                    'in Tast, not in Autotest. See: %s' %
                    (test_name, TAST_PSA_URL))


# TODO: reenable Contacts == !AUTHOR check once moblab fix is broadly used and delete this
# check entirely after metadata transition is complete.
def CheckOnlyOneContactSource(ctrl_data, ctrl_file_path):
    """
    Ensure there is exactly one source of Ownership data.

    @param ctrl_data: The control_data object for a test.
    @param test_name: A string with the name of the test.
    """
    if not (hasattr(ctrl_data, 'metadata') and 'contacts' in ctrl_data.metadata):
        raise ControlFileCheckerError(
                'Need "contacts" field in Metadata attribute : %s.' % ctrl_file_path)



def CheckSuiteLineRemoved(ctrl_file_path):
    """
    Check whether the SUITE line has been removed since it is obsolete.

    @param ctrl_file_path: The path to the control file.

    @raises: ControlFileCheckerError if check fails.
    """
    with open(ctrl_file_path, 'r') as f:
        for line in f.readlines():
            if line.startswith('SUITE'):
                raise ControlFileCheckerError(
                    'SUITE is an obsolete variable, please remove it from %s. '
                    'Instead, add suite:<your_suite> to the ATTRIBUTES field.'
                    % ctrl_file_path)


def CheckRetry(ctrl_data, test_name):
    """
    Check that any test in SUITES_NEED_RETRY has turned on retry.

    @param ctrl_data: The control_data object for a test.
    @param test_name: A string with the name of the test.

    @raises: ControlFileCheckerError if check fails.
    """
    if hasattr(ctrl_data, 'suite') and ctrl_data.suite:
        suites = set(x.strip() for x in ctrl_data.suite.split(',') if x.strip())
        if ctrl_data.job_retries < 2 and SUITES_NEED_RETRY.intersection(suites):
            raise ControlFileCheckerError(
                'Setting JOB_RETRIES to 2 or greater for test in '
                '%s is recommended. Please set it in the control '
                'file for %s.' % (' or '.join(SUITES_NEED_RETRY), test_name))


def CheckDependencies(ctrl_data, test_name):
    """
    Check if any dependencies of a test is required

    @param ctrl_data: The control_data object for a test.
    @param test_name: A string with the name of the test.

    @raises: ControlFileCheckerError if check fails.
    """
    if test_name.startswith(TESTS_NEED_ARC):
        if not DEPENDENCY_ARC in ctrl_data.dependencies:
            raise ControlFileCheckerError(
                    'DEPENDENCIES = \'arc\' for %s is needed' % test_name)


def CheckMetadataFormatting(ctrl_data, ctrl_file_path):
    """
    Check if the METADATA fields have valid formats.

    @param ctrl_data: The control_data object for a test.
    @param ctrl_file_path: The path to the control file.

    @raises: ControlFileCheckerError if check fails.
    """
    # Note: allowed metadata values should align with TestCaseMetadataInfo values.
    ALLOWED_METADATA_VALS = set([
            'contacts', 'doc', 'requirements', 'bug_component', 'criteria',
            'hw_agnostic', 'life_cycle_stage', 'variant_category'
    ])
    ALLOWED_LIFE_CYCLE_VALS = set([
            'production_ready', 'disabled', 'in_development', 'manual_only',
            'owner_monitored'
    ])

    # Flag unknown metadata fields, as it is probably a typo.
    extra_metadata_fields = set(ctrl_data.metadata) - ALLOWED_METADATA_VALS
    if extra_metadata_fields:
        warning = ('WARNING: Unknown metadata fields were '
                   'specified in %s.  Please remove '
                   '%s.') % (ctrl_file_path, ', '.join(extra_metadata_fields))
        raise ControlFileCheckerError(warning)

    # Contacts should be formatted like email addresses.
    EMAIL_REGEX = r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}'
    if 'contacts' in ctrl_data.metadata:
        values = ctrl_data.metadata['contacts']
        for c in values:
            if not re.fullmatch(EMAIL_REGEX, c):
                warning = ('WARNING: %s is not a valid email format! '
                           'Please fix %s.') % (c, ctrl_file_path)
                raise ControlFileCheckerError(warning)

    # Bug Components should only have an allowed prefix.
    ALLOWED_BUG_COMPONENT_PREFIXES = ['b:', 'crbug:']
    if 'bug_component' in ctrl_data.metadata:
        c = ctrl_data.metadata['bug_component']
        for prefix in ALLOWED_BUG_COMPONENT_PREFIXES:
            if c.startswith(prefix):
                break
        else:
            warning = ('WARNING: Bug components must start with [%s.] '
                       'Please fix %s.') % (', '.join(
                               ALLOWED_BUG_COMPONENT_PREFIXES), ctrl_file_path)
            raise ControlFileCheckerError(warning)

    # Life Cycle Stage should only have allowed values.
    if 'life_cycle_stage' in ctrl_data.metadata:
        value = ctrl_data.metadata['life_cycle_stage']
        if value not in ALLOWED_LIFE_CYCLE_VALS:
            warning = ('WARNING: %s is not an allowed '
                       'life_cycle_stage value. '
                       'Please fix %s.') % (value, ctrl_file_path)
            raise ControlFileCheckerError(warning)


def main(argv=None):
    """
    Checks if all control files that are a part of this commit conform to the
    ChromeOS autotest guidelines.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="Files to check.")
    args = parser.parse_args(argv)
    rv = 0

    # Parse the allowlist set from file, hardcode the filepath to the allowlist.
    path_attr_allowlist = os.path.join(common.autotest_dir,
                                       'site_utils/attribute_allowlist.txt')
    with open(path_attr_allowlist, 'r') as f:
        attr_allowlist = {
                line.strip()
                for line in f.readlines() if line.strip()
        }

    path_bvt_allowlist = os.path.join(common.autotest_dir,
                                      'site_utils/bvt_allowlist.txt')
    with open(path_bvt_allowlist, 'r') as f:
        bvt_allowlist = {
                line.strip()
                for line in f.readlines() if line.strip()
        }

    useflags = GetUseFlags()
    for file_path in args.files:
        control_file = re.search(r'.*/control(?:\..+)?$', file_path)
        if control_file:
            ctrl_file_path = control_file.group(0)
            CheckSuiteLineRemoved(ctrl_file_path)

            try:
                ctrl_data = control_data.parse_control(ctrl_file_path,
                                                       raise_warnings=True)
            except Exception as e:
                print(e, file=sys.stderr)
                rv = 1
                continue

            test_name = os.path.basename(os.path.split(file_path)[0])
            try:
                reporting_utils.BugTemplate.validate_bug_template(
                        ctrl_data.bug_template)
            except AttributeError:
                # The control file may not have bug template defined.
                pass

            checks = [
                    lambda: CheckSuites(ctrl_data, test_name, useflags),
                    lambda: CheckOnlyOneContactSource(ctrl_data, ctrl_file_path
                                                      ),
                    lambda: CheckValidAttr(ctrl_data, attr_allowlist,
                                           bvt_allowlist, test_name),
                    lambda: CheckRetry(ctrl_data, test_name),
                    lambda: CheckDependencies(ctrl_data, test_name),
                    lambda: CheckMetadataFormatting(ctrl_data, ctrl_file_path),
            ]
            for check in checks:
                try:
                    check()
                except ControlFileCheckerError as e:
                    print(e, file=sys.stderr)
                    rv = 1
    return rv


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
