# Lint as: python2, python3
# pylint: disable-msg=C0111
# Copyright 2008 Google LLC Released under the GPL v2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import ast
import logging
import shlex
import textwrap
import re
import six

from autotest_lib.client.common_lib import autotest_enum
from autotest_lib.client.common_lib import global_config
from autotest_lib.client.common_lib import priorities


REQUIRED_VARS = set(['name', 'test_type'])
# TODO: move author and doc to obsolete_vars when metadata is required
OBSOLETE_VARS = set(['experimental'])

CONTROL_TYPE = autotest_enum.AutotestEnum('Server', 'Client', start_value=1)
CONTROL_TYPE_NAMES = autotest_enum.AutotestEnum(*CONTROL_TYPE.names,
                                                string_values=True)

_SUITE_ATTRIBUTE_PREFIX = 'suite:'

CONFIG = global_config.global_config

# Default maximum test result size in kB.
DEFAULT_MAX_RESULT_SIZE_KB = CONFIG.get_config_value(
        'AUTOSERV', 'default_max_result_size_KB', type=int, default=20000)


class ControlVariableException(Exception):
    pass

def _validate_control_file_fields(control_file_path, control_file_vars,
                                  raise_warnings):
    """Validate the given set of variables from a control file.

    @param control_file_path: string path of the control file these were
            loaded from.
    @param control_file_vars: dict of variables set in a control file.
    @param raise_warnings: True iff we should raise on invalid variables.

    """
    diff = REQUIRED_VARS - set(control_file_vars)
    if diff:
        warning = ('WARNING: Not all required control '
                   'variables were specified in %s.  Please define '
                   '%s.') % (control_file_path, ', '.join(diff))
        if raise_warnings:
            raise ControlVariableException(warning)
        print(textwrap.wrap(warning, 80))

    obsolete = OBSOLETE_VARS & set(control_file_vars)
    if obsolete:
        warning = ('WARNING: Obsolete variables were '
                   'specified in %s.  Please remove '
                   '%s.') % (control_file_path, ', '.join(obsolete))
        if raise_warnings:
            raise ControlVariableException(warning)
        print(textwrap.wrap(warning, 80))


class ControlData(object):
    # Available TIME settings in control file, the list must be in lower case
    # and in ascending order, test running faster comes first.
    TEST_TIME_LIST = ['fast', 'short', 'default', 'medium', 'long', 'lengthy']
    TEST_TIME = autotest_enum.AutotestEnum(*TEST_TIME_LIST,
                                           string_values=False)

    @staticmethod
    def get_test_time_index(time):
        """
        Get the order of estimated test time, based on the TIME setting in
        Control file. Faster test gets a lower index number.
        """
        try:
            return ControlData.TEST_TIME.get_value(time.lower())
        except AttributeError:
            # Raise exception if time value is not a valid TIME setting.
            error_msg = '%s is not a valid TIME.' % time
            logging.error(error_msg)
            raise ControlVariableException(error_msg)


    def __init__(self, vars, path, raise_warnings=False):
        # Defaults
        self.path = path
        self.dependencies = set()
        # Whether this test needs the Telemetry framework present on the DUT.
        # Deliberately not expressed via DEPENDENCIES: entries there are matched
        # against DUT labels for scheduling, and Telemetry is a payload we
        # install on demand rather than a property of the hardware.
        self.require_telemetry = False
        self.metadata = {}
        # TODO(jrbarnette): This should be removed once outside
        # code that uses can be changed.
        self.experimental = False
        self.run_verify = True
        self.sync_count = 1
        self.test_parameters = set()
        self.test_category = ''
        self.test_class = ''
        self.job_retries = 0
        # Default to require server-side package. Unless require_ssp is
        # explicitly set to False, server-side package will be used for the
        # job.
        self.require_ssp = None
        self.attributes = set()
        self.max_result_size_KB = DEFAULT_MAX_RESULT_SIZE_KB
        self.priority = priorities.Priority.DEFAULT
        self.extended_timeout = None
        self.hw_deps = []
        self.variant_category = ''
        self.fast = True
        # This will only be honored via `test_that`, and not in lab (for now).
        self.py_version = None
        self.time = 'default'

        _validate_control_file_fields(self.path, vars, raise_warnings)

        for key, val in six.iteritems(vars):
            try:
                self.set_attr(key, val, raise_warnings)
            except Exception as e:
                if raise_warnings:
                    raise
                print('WARNING: %s; skipping' % e)

        self._patch_up_suites_from_attributes()


    @property
    def suite_tag_parts(self):
        """Return the part strings of the test's suite tag."""
        if hasattr(self, 'suite'):
            return [part.strip() for part in self.suite.split(',')]
        else:
            return []


    def set_attr(self, attr, val, raise_warnings=False):
        attr = attr.lower()
        try:
            set_fn = getattr(self, 'set_%s' % attr)
            set_fn(val)
        except AttributeError:
            # This must not be a variable we care about
            pass


    def _patch_up_suites_from_attributes(self):
        """Patch up the set of suites this test is part of.

        Legacy builds will not have an appropriate ATTRIBUTES field set.
        Take the union of suites specified via ATTRIBUTES and suites specified
        via SUITE.

        SUITE used to be its own variable, but now suites are taken only from
        the attributes.

        """

        suite_names = set()
        # Extract any suites we know ourselves to be in based on the SUITE
        # line.  This line is deprecated, but control files in old builds will
        # still have it.
        if hasattr(self, 'suite'):
            existing_suites = self.suite.split(',')
            existing_suites = [name.strip() for name in existing_suites]
            existing_suites = [name for name in existing_suites if name]
            suite_names.update(existing_suites)

        # Figure out if our attributes mention any suites.
        for attribute in self.attributes:
            if not attribute.startswith(_SUITE_ATTRIBUTE_PREFIX):
                continue
            suite_name = attribute[len(_SUITE_ATTRIBUTE_PREFIX):]
            suite_names.add(suite_name)

        # Rebuild the suite field if necessary.
        if suite_names:
            self.set_suite(','.join(sorted(list(suite_names))))


    def _set_string(self, attr, val):
        val = str(val)
        setattr(self, attr, val)


    def _set_option(self, attr, val, options):
        val = str(val)
        if val.lower() not in [x.lower() for x in options]:
            raise ValueError("%s must be one of the following "
                             "options: %s" % (attr,
                             ', '.join(options)))
        setattr(self, attr, val)


    def _set_bool(self, attr, val):
        val = str(val).lower()
        if val == "false":
            val = False
        elif val == "true":
            val = True
        else:
            msg = "%s must be either true or false" % attr
            raise ValueError(msg)
        setattr(self, attr, val)


    def _set_int(self, attr, val, min=None, max=None):
        val = int(val)
        if min is not None and min > val:
            raise ValueError("%s is %d, which is below the "
                             "minimum of %d" % (attr, val, min))
        if max is not None and max < val:
            raise ValueError("%s is %d, which is above the "
                             "maximum of %d" % (attr, val, max))
        setattr(self, attr, val)


    def _set_set(self, attr, val):
        val = str(val)
        items = [x.strip() for x in val.split(',') if x.strip()]
        setattr(self, attr, set(items))

    def _set_list(self, attr, val):
        setattr(self, attr, list(val))

    def _set_dict(self, attr, val):
        setattr(self, attr, val)

    def set_author(self, val):
        self._set_string('author', val)


    def set_dependencies(self, val):
        self._set_set('dependencies', val)


    def set_require_telemetry(self, val):
        self._set_bool('require_telemetry', val)


    def set_metadata(self, val):
        self._set_dict('metadata', val)

    def set_doc(self, val):
        self._set_string('doc', val)


    def set_name(self, val):
        self._set_string('name', val)


    def set_run_verify(self, val):
        self._set_bool('run_verify', val)


    def set_sync_count(self, val):
        self._set_int('sync_count', val, min=1)


    def set_suite(self, val):
        self._set_string('suite', val)


    def set_tast_test_exprs(self, val):
        if type(val) == list:
            self._set_string('tast_test_exprs',
                             " ".join([shlex.quote(v) for v in val]))

    def set_time(self, val):
        self._set_option('time', val, ControlData.TEST_TIME_LIST)


    def set_test_class(self, val):
        self._set_string('test_class', val.lower())


    def set_test_category(self, val):
        self._set_string('test_category', val.lower())


    def set_test_type(self, val):
        self._set_option('test_type', val, list(CONTROL_TYPE.names))


    def set_test_parameters(self, val):
        self._set_set('test_parameters', val)


    def set_job_retries(self, val):
        self._set_int('job_retries', val)


    def set_bug_template(self, val):
        if type(val) == dict:
            setattr(self, 'bug_template', val)


    def set_require_ssp(self, val):
        self._set_bool('require_ssp', val)


    def set_build(self, val):
        self._set_string('build', val)


    def set_builds(self, val):
        if type(val) == dict:
            setattr(self, 'builds', val)

    def set_max_result_size_kb(self, val):
        self._set_int('max_result_size_KB', val)

    def set_priority(self, val):
        self._set_int('priority', val)

    def set_fast(self, val):
        self._set_bool('fast', val)

    def set_update_type(self, val):
        self._set_string('update_type', val)

    def set_source_release(self, val):
        self._set_string('source_release', val)

    def set_target_release(self, val):
        self._set_string('target_release', val)

    def set_target_payload_uri(self, val):
        self._set_string('target_payload_uri', val)

    def set_source_payload_uri(self, val):
        self._set_string('source_payload_uri', val)

    def set_source_archive_uri(self, val):
        self._set_string('source_archive_uri', val)

    def set_attributes(self, val):
        self._set_set('attributes', val)

    def set_extended_timeout(self, val):
        """In seconds."""
        self._set_int('extended_timeout', val)

    def set_hw_deps(self, val):
        self._set_list('hw_deps', val)

    def set_py_version(self, val):
        """In majors, ie: 2 or 3."""
        self._set_int('py_version', val)

    def set_variant_category(self, val):
        self._set_string('variant_category', val.lower())


def _extract_const(expr):
    assert (expr.__class__ in (ast.Str, ast.Constant))
    val = expr.value if isinstance(expr, ast.Constant) else expr.s
    if six.PY2:
        assert (val.__class__ in (str, int, float, six.text_type))
    else:
        assert (val.__class__ in (str, int, float))
    return str(val).strip()


def _extract_dict(expr):
    assert (expr.__class__ == ast.Dict)
    assert (expr.keys.__class__ == list)
    cf_dict = {}
    for key, value in zip(expr.keys, expr.values):
        try:
            key = _extract_const(key)
            val = _extract_expression(value)
        except (AssertionError, ValueError):
            pass
        else:
            cf_dict[key] = val
    return cf_dict


def _extract_list(expr):
    assert (expr.__class__ == ast.List)
    list_values = []
    for value in expr.elts:
        try:
            list_values.append(_extract_expression(value))
        except (AssertionError, ValueError):
            pass
    return list_values


def _extract_name(expr):
    assert (expr.__class__ == ast.Name)
    assert (expr.id in ('False', 'True', 'None'))
    return str(expr.id)


def _extract_expression(expr):
    if expr.__class__ == ast.Str:
        return _extract_const(expr)
    if expr.__class__ == ast.Name:
        return _extract_name(expr)
    if expr.__class__ == ast.Dict:
        return _extract_dict(expr)
    if expr.__class__ == ast.List:
        return _extract_list(expr)
    if expr.__class__ == ast.Num:
        return expr.n
    if six.PY3 and expr.__class__ == ast.NameConstant:
        return expr.value
    if six.PY3 and expr.__class__ == ast.Constant:
        try:
            return expr.value.strip()
        except Exception:
            return expr.value
    raise ValueError('Unknown rval %s' % expr)


def _extract_assignment(n):
    assert (n.__class__ == ast.Assign)
    assert (len(n.targets) == 1)
    assert (n.targets[0].__class__ == ast.Name)
    val = _extract_expression(n.value)
    key = n.targets[0].id.lower()
    return (key, val)


def parse_control_string(control, raise_warnings=False, path=''):
    """Parse a control file from a string.

    @param control: string containing the text of a control file.
    @param raise_warnings: True iff ControlData should raise an error on
            warnings about control file contents.
    @param path: string path to the control file.

    """
    try:
        mod = ast.parse(control)
    except SyntaxError as e:
        logging.error('Syntax error (%s) while parsing control string:', e)
        lines = control.split('\n')
        for n, l in enumerate(lines):
            logging.error('Line %d: %s', n + 1, l)
        raise ControlVariableException("Error parsing data because %s" % e)
    return finish_parse(mod, path, raise_warnings)


def parse_control(path, raise_warnings=False):
    try:
        with open(path, 'r') as r:
            mod = ast.parse(r.read())
    except SyntaxError as e:
        raise ControlVariableException("Error parsing %s because %s" %
                                       (path, e))
    return finish_parse(mod, path, raise_warnings)


def _try_extract_assignment(node, variables):
    """Try to extract assignment from the given node.

    @param node: An Assign object.
    @param variables: Dictionary to store the parsed assignments.
    """
    try:
        key, val = _extract_assignment(node)
        variables[key] = val
    except (AssertionError, ValueError) as e:
        pass


def is_job_run_test_call(call_node):
    class visitor(ast.NodeVisitor):
        def __init__(self):
            super().__init__()
            self.name = None
            self.attr = None

        def visit_Name(self, node):
            self.name = node.id

        def visit_Attribute(self, node):
            self.attr = node.attr
            ast.NodeVisitor.generic_visit(self, node)

    v = visitor()
    v.visit(call_node)
    return v.name == "job" and v.attr == "run_test"


def extract_tast_test_exprs(call_node):
    class visitor(ast.NodeVisitor):
        def __init__(self):
            super().__init__()
            self.name = None
            self.attr = None

        def generic_visit(self, node):
            return ast.NodeVisitor.generic_visit(self, node)

        def visit_Name(self, node):
            return "SEE " + node.id

        def visit_Str(self, node):
            return node.s

        def visit_Constant(self, node):
            return node.value

        def visit_keyword(self, node):
            if node.arg == "test_exprs":
                return self.visit(node.value)
            return None

        def visit_List(self, node):
            result = []
            for i in node.elts:
                val = self.visit(i)
                if val is None:
                    raise Exception("Could not convert %s to str" % i)
                result.append(val)
            return result

    v = visitor()
    if len(call_node.args) > 0 and v.visit(call_node.args[0]) == 'tast':
        for k in call_node.keywords:
            rc = v.visit(k)
            if rc is not None:
                return rc
    return None


def finish_parse(mod, path, raise_warnings):
    assert (mod.__class__ == ast.Module)
    assert (mod.body.__class__ == list)

    variables = {}
    injection_variables = {}
    tast_test_exprs = None

    class runVisitor(ast.NodeVisitor):
        def generic_visit(self, node):
            ast.NodeVisitor.generic_visit(self, node)

        def visit_Call(self, node):
            if is_job_run_test_call(node.func):
                nonlocal tast_test_exprs
                tast_test_exprs = extract_tast_test_exprs(node)

    class visitor(ast.NodeVisitor):
        def generic_visit(self, node):
            pass

        def visit_Module(self, node):
            ast.NodeVisitor.generic_visit(self, node)

        def visit_Expr(self, node):
            ast.NodeVisitor.generic_visit(self, node)

        def visit_Assign(self, node):
            _try_extract_assignment(node, injection_variables)

        def visit_FunctionDef(self, node):
            if re.match(r'step\d+', node.name):
                vars_in_step = {}
                for sub_node in node.body:
                    _try_extract_assignment(sub_node, vars_in_step)
                if vars_in_step:
                    # Empty the vars collection so assignments from multiple steps
                    # won't be mixed.
                    variables.clear()
                    variables.update(vars_in_step)
            elif node.name == 'run':
                v = runVisitor()
                for sub_node in node.body:
                    v.visit(sub_node)

    v = visitor()
    v.visit(mod)
    if tast_test_exprs is not None:
        variables['tast_test_exprs'] = tast_test_exprs

    variables.update(injection_variables)
    return ControlData(variables, path, raise_warnings)
