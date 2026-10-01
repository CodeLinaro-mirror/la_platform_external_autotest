# Copyright 2023 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Functions for generating the actual controlfile content.

TODO(b/289468003): This still largely relies on the legacy script. Reimplement
the logic here and clean up glue code.
"""

from .bundle import Bundle
from .common import *


def _render_config(year,
                   name,
                   base_name,
                   test_func_name,
                   attributes,
                   dependencies,
                   extra_artifacts,
                   extra_artifacts_host,
                   job_retries,
                   max_result_size_kb,
                   revision,
                   build,
                   abi,
                   needs_push_media,
                   needs_cts_helpers,
                   enable_default_apps,
                   vm_force_max_resolution,
                   vm_tablet_mode,
                   tag,
                   uri,
                   servo_support_needed,
                   wifi_info_needed,
                   has_precondition_escape,
                   max_retries,
                   timeout,
                   run_template,
                   retry_template,
                   target_module,
                   target_plan,
                   test_length,
                   priority,
                   extra_args,
                   authkey,
                   camera_facing,
                   executable_test_count,
                   source_type='',
                   hw_deps=None):
    """Render config for generated controlfiles, by hard-coded some templates here.
    This is to replace jinja2 dependencies.
    """

    rendered_template = f'# Copyright {year} The ChromiumOS Authors\n' + \
    '# Use of this source code is governed by a BSD-style license that can be\n' + \
    '# found in the LICENSE file.\n\n' + \
    f'# __GENERATED_BY_GENERATE_CONTROLFILES_PY__:{source_type}\n\n' + \
    '# This file has been automatically generated. Do not edit!\n'
    if servo_support_needed:
        rendered_template += 'from autotest_lib.server import utils as server_utils\n'
    if wifi_info_needed:
        rendered_template += 'from autotest_lib.server.cros.tradefed import wifi_utils\n'
    if has_precondition_escape:
        rendered_template += 'import pipes\n'
    rendered_template += '\n'

    rendered_template += 'AUTHOR = \'n/a\'\n'
    rendered_template += f'NAME = \'{name}\'\n'
    rendered_template += 'METADATA = {\n' + \
        '    \"contacts\": [\"arc-cts-eng@google.com\"],\n' + \
        '    \"bug_component\": \"b:183644\",\n' + \
        '    \"criteria\": \"A part of Android CTS\",\n}\n'
    rendered_template += f'ATTRIBUTES = \'{attributes}\'\n'
    rendered_template += f'DEPENDENCIES = \'{dependencies}\'\n'
    if hw_deps is not None:
        hw_deps_str = ','.join(f"'{s}'" for s in hw_deps)
        rendered_template += f'HW_DEPS = [{hw_deps_str}]\n'
    rendered_template += f'JOB_RETRIES = {job_retries}\n'
    rendered_template += f'TEST_TYPE = \'server\'\n'
    rendered_template += f'TIME = \'{test_length}\'\n'
    rendered_template += f'MAX_RESULT_SIZE_KB = {max_result_size_kb}\n'
    if priority:
        rendered_template += f'PRIORITY = {priority}\n'
    rendered_template += 'DOC = \'n/a\'\n'
    if servo_support_needed:
        rendered_template += '\n# For local debugging, if your test setup doesn\'t have servo, REMOVE these\n'
        rendered_template += '# two lines.\n'
        rendered_template += 'args_dict = server_utils.args_to_dict(args)\n'
        rendered_template += 'servo_args = hosts.CrosHost.get_servo_arguments(args_dict)\n'
    rendered_template += '\n'
    rendered_template += f'def {test_func_name}(machine):\n'
    if servo_support_needed:
        rendered_template += '    # REMOVE \'servo_args=servo_args\' arg for local debugging if your test\n'
        rendered_template += '    # setup doesn\'t have servo.\n'
        rendered_template += '    try:\n'
        rendered_template += '        host_list = [hosts.create_host(machine, servo_args=servo_args)]\n'
        rendered_template += '    except:\n'
        rendered_template += '        # Just ignore any servo setup flakiness.\n'
        rendered_template += '        host_list = [hosts.create_host(machine)]\n'
    else:
        rendered_template += '    host_list = [hosts.create_host(machine)]\n'
    if wifi_info_needed:
        rendered_template += '    ssid, wifipass = wifi_utils.get_wifi_ssid_pass(machine)\n'
    rendered_template += '    job.run_test(\n'
    rendered_template += f'        \'{base_name}\',\n'
    if camera_facing and camera_facing != 'nocamera':
        rendered_template += f'        camera_facing=\'{camera_facing}\',\n'
        rendered_template += '        cmdline_args=args,\n'
    rendered_template += '        hosts=host_list,\n'
    rendered_template += '        iterations=1,\n'
    if max_retries != None:
        rendered_template += f'        max_retry={max_retries},\n'
    if enable_default_apps:
        rendered_template += '        enable_default_apps=True,\n'
    if vm_force_max_resolution:
        rendered_template += '        vm_force_max_resolution=True,\n'
    if vm_tablet_mode:
        rendered_template += '        vm_tablet_mode=True,\n'
    if needs_push_media:
        rendered_template += f'        needs_push_media={needs_push_media},\n'
    if needs_cts_helpers:
        rendered_template += f'        use_helpers={needs_cts_helpers},\n'
    rendered_template += f'        tag=\'{tag}\',\n'
    rendered_template += f'        test_name=\'{name}\',\n'
    if authkey:
        rendered_template += f'        authkey=\'{authkey}\',\n'
    rendered_template += f'        run_template={run_template},\n'
    rendered_template += f'        retry_template={retry_template},\n'
    rendered_template += '        target_module='
    if target_module:
        rendered_template += f'\'{target_module}\',\n'
    else:
        rendered_template += 'None,\n'
    rendered_template += '        target_plan='
    if target_plan:
        rendered_template += f'\'{target_plan}\',\n'
    else:
        rendered_template += 'None,\n'
    if abi:
        rendered_template += f'        bundle=\'{abi}\',\n'
    if extra_artifacts:
        rendered_template += f'        extra_artifacts={extra_artifacts},\n'
    if extra_artifacts_host:
        rendered_template += f'        extra_artifacts_host={extra_artifacts_host},\n'
    if uri:
        rendered_template += f'        uri=\'{uri}\',\n'
    for arg in extra_args:
        rendered_template += f'        {arg},\n'
    if servo_support_needed:
        rendered_template += '        hard_reboot_on_failure=True,\n'
    if camera_facing:
        rendered_template += '        retry_manual_tests=True,\n'
    if executable_test_count:
        rendered_template += f'        executable_test_count={executable_test_count},\n'
    rendered_template += f'        timeout={timeout})\n\n'
    rendered_template += f'parallel_simple({test_func_name}, machines)\n'
    return rendered_template


def _format_collect_cmd(config,
                        is_public,
                        abi_to_run,
                        retry,
                        is_hardware=False,
                        is_camera=False):
    """Returns a list specifying tokens for tradefed to list all tests."""
    if retry:
        return None
    cmd = ['run', 'commandAndExit', 'collect-tests-only']
    if config['TRADEFED_DISABLE_REBOOT_ON_COLLECTION']:
        cmd += ['--disable-reboot']
    if is_camera:
        cmd += ['--module', 'CtsCameraTestCases']
    elif is_hardware:
        cmd.append('--subplan')
        cmd.append('cts-hardware')
    if not is_camera:
        for m in config.get('COLLECT_EXCLUDE_MODULES', []):
            cmd.extend(['--exclude-filter', m])
    for m in config['MEDIA_MODULES']:
        cmd.append('--module-arg')
        cmd.append('%s:skip-media-download:true' % m)
    if (
            not is_public
            and not config.get('NEEDS_DYNAMIC_CONFIG_ON_COLLECTION', True)):
        cmd.append('--dynamic-config-url=')
    if abi_to_run:
        cmd += ['--abi', abi_to_run]
    return cmd


def _is_parameterized_module(module):
    """Determines if the given module is a parameterized module."""
    return '[' in module


def _format_modules_cmd(config,
                        is_public,
                        abi_to_run,
                        shard=(0, 1),
                        modules=None,
                        retry=False,
                        whole_module_set=None,
                        is_hardware=False):
    """Returns list of command tokens for tradefed."""
    if retry:
        assert (config['TRADEFED_RETRY_COMMAND'] == 'cts'
                or config['TRADEFED_RETRY_COMMAND'] == 'retry')

        cmd = [
                'run', 'commandAndExit', config['TRADEFED_RETRY_COMMAND'],
                '--retry', '{session_id}'
        ]
    else:
        # For runs create a logcat file for each individual failure.
        cmd = ['run', 'commandAndExit', config['TRADEFED_CTS_COMMAND']]

        special_cmd = config.get_special_command_line(modules)
        if special_cmd:
            if is_hardware:
                # For hardware suite we want to exclude [instant] modules.
                filtered = []
                i = 0
                while i < len(special_cmd):
                    if (special_cmd[i] == '--include-filter'
                                and '[instant]' in special_cmd[i + 1]):
                        i += 2
                    elif (special_cmd[i] == '--module'
                          and i + 3 < len(special_cmd)
                          and special_cmd[i + 2] == '--test'):
                        # [--module, x, --test, y] ==> [--include-filter, "x y"]
                        # because --module implicitly include [instant] modules
                        filtered.append('--include-filter')
                        filtered.append(
                                '%s %s' %
                                (special_cmd[i + 1], special_cmd[i + 3]))
                        i += 4
                    elif special_cmd[i] == '--module':
                        # [--module, x] ==> [--include-filter, x]
                        filtered.append('--include-filter')
                        filtered.append(special_cmd[i + 1])
                        i += 2
                    else:
                        filtered.append(special_cmd[i])
                        i += 1
                special_cmd = filtered
            cmd.extend(special_cmd)
        elif 'all' in modules:
            pass
        elif len(modules) == 1 and not is_hardware:
            cmd += ['--module', list(modules)[0]]
        else:
            if whole_module_set is None:
                assert (config['TRADEFED_CTS_COMMAND'] != 'cts-instant'), \
                       'cts-instant cannot include multiple modules'
                # We run each module with its own --include-filter option.
                # https://source.android.com/compatibility/cts/run
                for module in sorted(modules):
                    # b/196756614 32-bit jobs should skip [parameter] modules.
                    if _is_parameterized_module(module) and abi_to_run in [
                            'x86', 'armeabi-v7a'
                    ]:
                        continue
                    cmd += ['--include-filter', module]
            else:
                # CTS-Instant does not support --include-filter due to
                # its implementation detail. Instead, exclude the complement.
                for module in sorted(whole_module_set - set(modules)):
                    cmd += ['--exclude-filter', module]

        if not special_cmd and config.should_set_simple_cache_semantics():
            # b/369256917#comment10: Disable rigorous cache checks to make sure
            # MediaPreparer always skips downloading.
            for m in config.get_media_modules(modules):
                cmd.extend(['--module-arg',
                            f'{m}:simple-caching-semantics:true'])

        if shard != (0, 1):
            cmd += [
                    '--shard-index',
                    str(shard[0]), '--shard-count',
                    str(shard[1])
            ]

        # For runs create a logcat file for each individual failure.
        # Not needed on moblab, nobody is going to look at them.
        if (not modules.intersection(config['DISABLE_LOGCAT_ON_FAILURE'])
                    and not is_public
                    and config['TRADEFED_CTS_COMMAND'] != 'gts'):
            cmd.append('--logcat-on-failure')

        if config['TRADEFED_IGNORE_BUSINESS_LOGIC_FAILURE']:
            cmd.append('--ignore-business-logic-failure')

    if config['TRADEFED_DISABLE_REBOOT']:
        cmd.append('--disable-reboot')
    if (config['TRADEFED_MAY_SKIP_DEVICE_INFO']
                and not modules.intersection(config['NEEDS_DEVICE_INFO'])):
        cmd.append('--skip-device-info')
    if abi_to_run:
        cmd += ['--abi', abi_to_run]
    # If NEEDS_DYNAMIC_CONFIG is set, disable the feature except on the modules
    # that explicitly set as needed.
    if (not is_public and config.get('NEEDS_DYNAMIC_CONFIG')
                and not modules.intersection(config['NEEDS_DYNAMIC_CONFIG'])):
        cmd.append('--dynamic-config-url=')

    return cmd


def _get_run_template(config,
                      modules,
                      is_public,
                      is_collect,
                      retry=False,
                      abi_to_run=None,
                      shard=(0, 1),
                      whole_module_set=None,
                      is_hardware=False):
    """Command to run the modules specified by a control file."""
    # TODO(kinaba): `_ALL` phony module is no longer used anywhere.
    # Clean it up together with all the other occurrences.
    is_all = 'all' in modules
    if is_all:
        return None
    elif is_collect:
        return _format_collect_cmd(config,
                                   is_public,
                                   abi_to_run,
                                   retry=retry,
                                   is_hardware=is_hardware,
                                   is_camera='camerabox' in list(modules)[0])
    else:
        return _format_modules_cmd(config,
                                   is_public,
                                   abi_to_run,
                                   shard,
                                   modules,
                                   retry=retry,
                                   whole_module_set=whole_module_set,
                                   is_hardware=is_hardware)


def _get_retry_template(config, modules, is_public, is_collect):
    """Command to retry the failed modules as specified by a control file."""
    return _get_run_template(config,
                             modules,
                             is_public,
                             is_collect,
                             retry=True)


def _get_extension(group: ModuleGroup, config: Config, bundle: Bundle) -> str:
    abi = bundle.abi or group.get('abi')
    return config.get_extension(
            group['basename'],
            bundle.abi or group.get('abi'),
            bundle.revision,
            is_public=bundle.source_type == 'MOBLAB',
            camera_facing=group.get('camera_facing'),
            hardware_suite=group.get('hardware_suite', False),
            abi_bits=group.get('abi_bits'),
            shard=group.get('shard', (0, 1)),
            vm_force_max_resolution=group.get('vm_force_max_resolution',
                                              False),
            vm_tablet_mode=group.get('vm_tablet_mode', False))


def get_controlfile_name(group: ModuleGroup, config: Config,
                         bundle: Bundle) -> str:
    """Returns the name of the control file.

    Args:
        group: the module group corresponding to the control file.
        bundle: the bundle corresponding to the control file.

    Returns:
        The name of the control file.
    """
    extension = _get_extension(group, config, bundle)
    return f"control.{extension}"


def get_controlfile_content(group: ModuleGroup, config: Config,
                            bundle: Bundle) -> str:
    """Returns the contents of the control file.

    Args:
        group: the module group corresponding to the control file.
        config: the config dictionary.
        bundle: the bundle corresponding to the control file.

    Returns:
        The contents of the control file.
    """
    modules = group['modules']
    suites = sorted(group.get('suites', []))
    revision = bundle.revision
    build = bundle.build
    abi = bundle.abi or group.get('abi')
    uri = None if bundle.source_type == 'MOBLAB' else bundle.source_type
    abi_bits = group.get('abi_bits')
    is_public = bundle.source_type == 'MOBLAB'
    attributes = ', '.join(suites)
    shard = group.get('shard', (0, 1))
    camera_facing = group.get('camera_facing')
    hardware_suite = group.get('hardware_suite', False)
    subplan = group.get('subplan')
    vm_force_max_resolution = group.get('vm_force_max_resolution', False)
    vm_tablet_mode = group.get('vm_tablet_mode', False)

    target_module = group['basename']
    # Special case to match old behavior.
    # TODO cleanup this
    if (target_module in config.get_collect_modules(is_public)
                or target_module == 'all'):
        target_module = None
    is_collect = (len(modules) == 1
                  and list(modules)[0] in config.get_collect_modules(
                          is_public, hardware_suite))

    tag = _get_extension(group, config, bundle)
    name = f"{config['TEST_NAME']}.{tag}"
    abi_to_run = {
            ("arm", 32): 'armeabi-v7a',
            ("arm", 64): 'arm64-v8a',
            ("x86", 32): 'x86',
            ("x86", 64): 'x86_64'
    }.get((abi, abi_bits), None)
    whole_module_set = None
    executable_test_count = None

    return _render_config(
            year=config['COPYRIGHT_YEAR'],
            name=name,
            base_name=config['TEST_NAME'],
            test_func_name=config['CONTROLFILE_TEST_FUNCTION_NAME'],
            attributes=attributes,
            dependencies=config.get_dependencies(modules, abi, is_public,
                                                 camera_facing),
            extra_artifacts=config.get_extra_artifacts(modules),
            extra_artifacts_host=config.get_extra_artifacts_host(modules),
            job_retries=config.get_job_retries(modules, is_public, suites),
            max_result_size_kb=config.get_max_result_size_kb(
                    modules, is_public),
            revision=revision,
            build=build,
            abi=abi,
            needs_push_media=bool(config.get_media_modules(modules)),
            needs_cts_helpers=config.needs_cts_helpers(modules),
            enable_default_apps=config.enable_default_apps(modules),
            vm_force_max_resolution=vm_force_max_resolution,
            vm_tablet_mode=vm_tablet_mode,
            tag=tag,
            uri=uri,
            servo_support_needed=config.servo_support_needed(
                    modules, is_public),
            wifi_info_needed=config.wifi_info_needed(modules, is_public),
            has_precondition_escape=config.has_precondition_escape(
                    modules, is_public),
            max_retries=config.get_max_retries(modules, abi, suites, is_public,
                                               shard),
            timeout=config.calculate_timeout(modules, suites),
            run_template=_get_run_template(config,
                                           modules,
                                           is_public,
                                           is_collect,
                                           abi_to_run=config.get(
                                                   'REPRESENTATIVE_ABI',
                                                   {}).get(abi, abi_to_run),
                                           shard=shard,
                                           whole_module_set=whole_module_set,
                                           is_hardware=hardware_suite),
            retry_template=_get_retry_template(config, modules, is_public,
                                               is_collect),
            target_module=target_module,
            target_plan=subplan,
            test_length=config.get_test_length(modules),
            priority=config.get_test_priority(modules, is_public),
            extra_args=config.get_extra_args(modules, is_public),
            authkey=config.get_authkey(is_public),
            camera_facing=camera_facing,
            executable_test_count=executable_test_count,
            source_type=bundle.source_type,
            hw_deps=config.get_tauto_hw_deps(bundle.source_type),
    )
