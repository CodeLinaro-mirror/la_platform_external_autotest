# Copyright 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Server side Bluetooth audio tests."""

import logging
import time

from autotest_lib.client.bin import utils
from autotest_lib.client.common_lib import error
from autotest_lib.client.cros.bluetooth.bluetooth_audio_test_data import (
        A2DP, HFP_WBS, HFP_NBS, audio_test_data)
from autotest_lib.server.cros.bluetooth.bluetooth_adapter_tests import (
        BluetoothAdapterTests, test_retry_and_log)


class BluetoothAdapterAudioTests(BluetoothAdapterTests):
    """Server side Bluetooth adapter audio test class."""

    DEVICE_TYPE = 'BLUETOOTH_AUDIO'
    FREQUENCY_TOLERANCE_RATIO = 0.01
    WAIT_DAEMONS_READY_SECS = 1

    def _get_pulseaudio_bluez_source(self, get_source_method, device,
                                     test_profile):
        """Get the specified bluez device number in the pulseaudio source list.

        @param get_source_method: the method to get distinct bluez source
        @param device: the bluetooth peer device
        @param test_profile: the test profile used, A2DP, HFP_WBS or HFP_NBS

        @returns: True if the specified bluez source is derived
        """
        sources = device.ListSources(test_profile)
        logging.debug('ListSources()\n%s', sources)
        self.bluez_source = get_source_method(test_profile)
        result = bool(self.bluez_source)
        if result:
            logging.debug('bluez_source device number: %s', self.bluez_source)
        else:
            logging.debug('waiting for bluez_source ready in pulseaudio...')
        return result


    def _get_pulseaudio_bluez_sink(self, get_sink_method, device, test_profile):
        """Get the specified bluez device number in the pulseaudio sink list.

        @param get_sink_method: the method to get distinct bluez sink
        @param device: the bluetooth peer device
        @param test_profile: the test profile used, A2DP, HFP_WBS or HFP_NBS

        @returns: True if the specified bluez sink is derived
        """
        sinks = device.ListSinks(test_profile)
        logging.debug('ListSinks()\n%s', sinks)
        self.bluez_sink = get_sink_method(test_profile)
        result = bool(self.bluez_sink)
        if result:
            logging.debug('bluez_sink device number: %s', self.bluez_sink)
        else:
            logging.debug('waiting for bluez_sink ready in pulseaudio...')
        return result


    def _get_pulseaudio_bluez_source_a2dp(self, device, test_profile):
        """Get the a2dp bluez source device number.

        @param device: the bluetooth peer device
        @param test_profile: the test profile used, A2DP, HFP_WBS or HFP_NBS

        @returns: True if the specified a2dp bluez source is derived
        """
        return self._get_pulseaudio_bluez_source(
                device.GetBluezSourceA2DPDevice, device, test_profile)


    def _get_pulseaudio_bluez_source_hfp(self, device, test_profile):
        """Get the hfp bluez source device number.

        @param device: the bluetooth peer device
        @param test_profile: the test profile used, A2DP, HFP_WBS or HFP_NBS

        @returns: True if the specified hfp bluez source is derived
        """
        return self._get_pulseaudio_bluez_source(
                device.GetBluezSourceHFPDevice, device, test_profile)


    def _get_pulseaudio_bluez_sink_hfp(self, device, test_profile):
        """Get the hfp bluez sink device number.

        @param device: the bluetooth peer device
        @param test_profile: the test profile used, A2DP, HFP_WBS or HFP_NBS

        @returns: True if the specified hfp bluez sink is derived
        """
        return self._get_pulseaudio_bluez_sink(
                device.GetBluezSinkHFPDevice, device, test_profile)


    def _check_audio_frames_legitimacy(self, audio_test_data, recording_device):
        """Check if audio frames in the recorded file are legitimate.

        For a wav file, a simple check is to make sure the recorded audio file
        is not empty.

        For a raw file, a simple check is to make sure the recorded audio file
        are not all zeros.

        @param audio_test_data: a dictionary about the audio test data
                defined in client/cros/bluetooth/bluetooth_audio_test_data.py
        @param recording_device: which device recorded the audio,
                possible values are 'recorded_by_dut' or 'recorded_by_peer'

        @returns: True if audio frames are legitimate.
        """
        result = self.bluetooth_facade.check_audio_frames_legitimacy(
                audio_test_data, recording_device)
        if not result:
            self.results = {'audio_frames_legitimacy': 'empty or all zeros'}
            logging.error('The recorded audio file is empty or all zeros.')
        return result


    def _check_frequency(self, test_profile, recorded_freq, expected_freq):
        """Check if the recorded frequency is within tolerance.

        @param test_profile: the test profile used, A2DP, HFP_WBS or HFP_NBS
        @param recorded_freq: the frequency of recorded audio
        @param expected_freq: the expected frequency

        @returns: True if the recoreded frequency falls within the tolerance of
                  the expected frequency
        """
        tolerance = expected_freq * self.FREQUENCY_TOLERANCE_RATIO
        return abs(expected_freq - recorded_freq) <= tolerance


    def _check_primary_frequencies(self, test_profile, audio_test_data,
                                   recording_device):
        """Check if the recorded frequencies meet expectation.

        @param test_profile: the test profile used, A2DP, HFP_WBS or HFP_NBS
        @param audio_test_data: a dictionary about the audio test data
                defined in client/cros/bluetooth/bluetooth_audio_test_data.py
        @param recording_device: which device recorded the audio,
                possible values are 'recorded_by_dut' or 'recorded_by_peer'

        @returns: True if the recorded frequencies of all channels fall within
                the tolerance of expected frequencies
        """
        recorded_frequencies = self.bluetooth_facade.get_primary_frequencies(
                audio_test_data, recording_device)
        expected_frequencies = audio_test_data['frequencies']
        final_result = True
        self.results = dict()

        if len(recorded_frequencies) < len(expected_frequencies):
            logging.error('recorded_frequencies: %s, expected_frequencies: %s',
                          str(recorded_frequencies), str(expected_frequencies))
            final_result = False
        else:
            for channel, expected_freq in enumerate(expected_frequencies):
                recorded_freq = recorded_frequencies[channel]
                ret_val = self._check_frequency(
                        test_profile, recorded_freq, expected_freq)
                pass_fail_str = 'pass' if ret_val else 'fail'
                result = ('primary frequency %d (expected %d): %s' %
                          (recorded_freq, expected_freq, pass_fail_str))
                self.results['Channel %d' % channel] = result
                logging.info('Channel %d: %s', channel, result)

                if not ret_val:
                    final_result = False

        logging.debug(str(self.results))
        if not final_result:
            logging.error('Failure at checking primary frequencies')
        return final_result


    def _poll_for_condition(self, condition, timeout=20, sleep_interval=1,
                            desc='waiting for condition'):
        try:
            utils.poll_for_condition(condition=condition,
                                     timeout=timeout,
                                     sleep_interval=sleep_interval,
                                     desc=desc)
        except Exception as e:
            raise error.TestError('Exception occurred when %s' % desc)


    def initialize_bluetooth_audio(self, device, test_profile):
        """Initialize the Bluetooth audio task.

        Note: pulseaudio is not stable. Need to restart it in the beginning.

        @param device: the bluetooth peer device
        @param test_profile: the test profile used, A2DP, HFP_WBS or HFP_NBS

        """
        if not device.StartPulseaudio(test_profile):
            raise error.TestError('Failed to start pulseaudio.')
        logging.debug('pulseaudio is started.')

        if test_profile in (HFP_WBS, HFP_NBS):
            if device.StartOfono():
                logging.debug('ofono is started.')
            else:
                raise error.TestError('Failed to start ofono.')
        elif device.StopOfono():
            logging.debug('ofono is stopped.')
        else:
            logging.warn('Failed to stop ofono. Ignored.')

        # Need time to complete starting services.
        time.sleep(self.WAIT_DAEMONS_READY_SECS)


    def cleanup_bluetooth_audio(self, device, test_profile):
        """Cleanup for Bluetooth audio.

        @param device: the bluetooth peer device
        @param test_profile: the test profile used, A2DP, HFP_WBS or HFP_NBS

        """
        if device.StopPulseaudio():
            logging.debug('pulseaudio is stopped.')
        else:
            logging.warn('Failed to stop pulseaudio. Ignored.')

        if device.StopOfono():
            logging.debug('ofono is stopped.')
        else:
            logging.warn('Failed to stop ofono. Ignored.')


    def initialize_bluetooth_player(self, device):
        """Initialize the Bluetooth media player.

        @param device: the Bluetooth peer device.

        """
        if not device.ExportMediaPlayer():
            raise error.TestError('Failed to export media player.')
        logging.debug('mpris-proxy is started.')

        # Wait for player to show up and observed by playerctl.
        desc='waiting for media player'
        self._poll_for_condition(
                lambda: bool(device.GetExportedMediaPlayer()), desc=desc)


    def cleanup_bluetooth_player(self, device):
        """Cleanup for Bluetooth media player.

        @param device: the bluetooth peer device.

        """
        device.UnexportMediaPlayer()


    def initialize_hfp(self, device, test_profile, test_data,
                       recording_device, bluez_function):
        """
        Initial set up for hfp tests.

        Setup that is required for all hfp tests where
        dut is either source or sink. Selects input device, starts recording,
        and lastly it waits for pulseaudio bluez source/sink.

        @param device: the bluetooth peer device
        @param test_profile: the test profile used, A2DP, HFP_WBS or HFP_NBS
        @param test_data: a dictionary about the audio test data defined in
                client/cros/bluetooth/bluetooth_audio_test_data.py
        @param recording_device: which device recorded the audio, possible
                values are 'recorded_by_dut' or 'recorded_by_peer'
        @param bluez_function: the appropriate bluez hfp function either
                _get_pulseaudio_bluez_source_hfp or
                _get_pulseaudio_bluez_sink_hfp depending on the role of the dut
        """
        device_type = "DUT" if recording_device == "recorded_by_dut" else "Peer"
        dut_role = "sink" if recording_device == "recorded_by_dut" else "source"

        # Select audio input device.
        desc='waiting for cras to select audio input device'
        logging.debug(desc)
        self._poll_for_condition(
                lambda: self.bluetooth_facade.select_input_device(device.name),
                desc=desc)

        # Enable HFP profile.
        logging.debug('Start recording audio on {}'.format(device_type))
        if not self.bluetooth_facade.start_capturing_audio_subprocess(
                test_data, recording_device):
            raise error.TestError(
                '{} failed to start capturing audio.'.format(device_type)
            )

        # Wait for pulseaudio bluez hfp source/sink
        desc='waiting for pulseaudio bluez hfp {}'.format(dut_role)
        logging.debug(desc)
        self._poll_for_condition(lambda: bluez_function(device, test_profile),
                                 desc=desc)


    def hfp_record_on_dut(self, device, test_profile, test_data):
        """
        Play audio from test_data dictionary from peer device to dut and record
        on dut.

        @param device: the bluetooth peer device
        @param test_profile: the test profile used, A2DP, HFP_WBS or HFP_NBS
        @param test_data: a dictionary about the audio test data defined in
                client/cros/bluetooth/bluetooth_audio_test_data.py

        @returns: True if the recorded audio frames are legitimate, False
                if they are not, ie. it did not record.
        """
        # Select audio input device.
        logging.debug('Select input device')
        if not self.bluetooth_facade.select_input_device(device.name):
            raise error.TestError('DUT failed to select audio input device.')

        # Start playing audio on chameleon.
        logging.debug('Start playing audio on Pi')
        if not device.StartPlayingAudioSubprocess(test_profile):
            err = 'Failed to start playing audio file on the peer device'
            raise error.TestError(err)

        time.sleep(test_data['duration'])

        # Stop playing audio on chameleon.
        logging.debug('Stop playing audio on Pi')
        if not device.StopPlayingAudioSubprocess():
            err = 'Failed to stop playing audio on the peer device'
            raise error.TestError(err)

        # Disable HFP profile.
        logging.debug('Stop recording audio on DUT')
        if not self.bluetooth_facade.stop_capturing_audio_subprocess():
            raise error.TestError('DUT failed to stop capturing audio.')

        # Check if the audio frames in the recorded file are legitimate.
        return self._check_audio_frames_legitimacy(test_data, 'recorded_by_dut')


    def hfp_record_on_peer(self, device, test_profile, test_data):
        """
        Play audio from test_data dictionary from dut to bt peer and record
        on bt peer.

        @param device: the bluetooth peer device
        @param test_profile: the test profile used, A2DP, HFP_WBS or HFP_NBS
        @param test_data: a dictionary about the audio test data defined in
                client/cros/bluetooth/bluetooth_audio_test_data.py

        @returns: True if the recorded audio frames are legitimate, False
                if they are not, ie. it did not record.
        """
        logging.debug('Start recording audio on Pi')
        # Start recording audio on the peer Bluetooth audio device.
        if not device.StartRecordingAudioSubprocess(test_profile):
            raise error.TestError(
                    'Failed to record on the peer Bluetooth audio device.')

        # Play audio on the DUT in a non-blocked way.
        # If there are issues, cras_test_client playing back might be blocked
        # forever. We would like to avoid the testing procedure from that.
        logging.debug('Start playing audio')
        if not self.bluetooth_facade.start_playing_audio_subprocess(test_data):
            raise error.TestError('DUT failed to play audio.')

        time.sleep(test_data['duration'])

        logging.debug('Stop recording audio on Pi')
        # Stop recording audio on the peer Bluetooth audio device.
        if not device.StopRecordingingAudioSubprocess():
            msg = 'Failed to stop recording on the peer Bluetooth audio device'
            logging.error(msg)

        # Disable HFP profile.
        logging.debug('Stop recording audio on DUT')
        if not self.bluetooth_facade.stop_capturing_audio_subprocess():
            raise error.TestError('DUT failed to stop capturing audio.')

        # Stop playing audio on DUT.
        logging.debug('Stop playing audio on DUT')
        if not self.bluetooth_facade.stop_playing_audio_subprocess():
            raise error.TestError('DUT failed to stop playing audio.')

        # Copy the recorded audio file to the DUT for spectrum analysis.
        logging.debug('Scp to DUT')
        recorded_file = test_data['recorded_by_peer']
        device.ScpToDut(recorded_file, recorded_file, self.host.ip)

        # Check if the audio frames in the recorded file are legitimate.
        return self._check_audio_frames_legitimacy(test_data,
                                                   'recorded_by_peer')


    # ---------------------------------------------------------------
    # Definitions of all bluetooth audio test cases
    # ---------------------------------------------------------------


    @test_retry_and_log(False)
    def test_a2dp_sinewaves(self, device):
        """Test Case: a2dp sinewaves

        @param device: the bluetooth peer device

        @returns: True if the recorded primary frequency is within the
                  tolerance of the playback sine wave frequency.

        """
        a2dp_test_data = audio_test_data[A2DP]

        # Wait for pulseaudio a2dp bluez source
        desc='waiting for pulseaudio a2dp bluez source'
        logging.debug(desc)
        self._poll_for_condition(
                lambda: self._get_pulseaudio_bluez_source_a2dp(device, A2DP),
                desc=desc)

        # Start recording audio on the peer Bluetooth audio device.
        logging.debug('Start recording a2dp')
        if not device.StartRecordingAudioSubprocess(A2DP):
            raise error.TestError(
                    'Failed to record on the peer Bluetooth audio device.')

        # Play stereo audio on the DUT.
        logging.debug('Play audio')
        if not self.bluetooth_facade.play_audio(a2dp_test_data):
            raise error.TestError('DUT failed to play audio.')

        # Stop recording audio on the peer Bluetooth audio device.
        logging.debug('Stop recording a2dp')
        if not device.StopRecordingingAudioSubprocess():
            msg = 'Failed to stop recording on the peer Bluetooth audio device'
            logging.error(msg)

        # Copy the recorded audio file to the DUT for spectrum analysis.
        logging.debug('Scp recorded file')
        recorded_file = a2dp_test_data['recorded_by_peer']
        device.ScpToDut(recorded_file, recorded_file, self.host.ip)

        # Check if the audio frames in the recorded file are legitimate.
        if not self._check_audio_frames_legitimacy(a2dp_test_data,
                                                   'recorded_by_peer'):
            return False

        # Check if the primary frequencies of recorded file meet expectation.
        check_freq_result = self._check_primary_frequencies(
                A2DP, a2dp_test_data, 'recorded_by_peer')
        return check_freq_result


    @test_retry_and_log(False)
    def test_hfp_dut_as_source(self, device, test_profile):
        """Test Case: hfp sinewave streaming from dut to peer device

        @param device: the bluetooth peer device
        @param test_profile: which test profile is used, HFP_WBS or HFP_NBS

        @returns: True if the recorded primary frequency is within the
                  tolerance of the playback sine wave frequency.
        """
        hfp_test_data = audio_test_data[test_profile]

        self.initialize_hfp(device, test_profile, hfp_test_data,
                            'recorded_by_peer',
                            self._get_pulseaudio_bluez_source_hfp)

        if not self.hfp_record_on_peer(device, test_profile, hfp_test_data):
            return False

        # Check if the primary frequencies of recorded file meet expectation.
        check_freq_result = self._check_primary_frequencies(
                test_profile, hfp_test_data, 'recorded_by_peer')
        return check_freq_result


    @test_retry_and_log(False)
    def test_hfp_dut_as_sink(self, device, test_profile):
        """Test Case: hfp sinewave streaming from peer device to dut

        @param device: the bluetooth peer device
        @param test_profile: which test profile is used, HFP_WBS or HFP_NBS

        @returns: True if the recorded primary frequency is within the
                  tolerance of the playback sine wave frequency.

        """
        hfp_test_data = audio_test_data[test_profile]

        # Set up hfp test to record on dut
        self.initialize_hfp(device, test_profile, hfp_test_data,
                            'recorded_by_dut',
                            self._get_pulseaudio_bluez_sink_hfp)

        # Record audio on dut play from pi, returns true if anything recorded
        if not self.hfp_record_on_dut(device, test_profile, hfp_test_data):
            return False

        # Check if the primary frequencies of recorded file meet expectation.
        check_freq_result = self._check_primary_frequencies(
                test_profile, hfp_test_data, 'recorded_by_dut')
        return check_freq_result


    @test_retry_and_log(False)
    def test_avrcp_commands(self, device):
        """Test Case: Test AVRCP commands issued by peer can be received at DUT

        The very first AVRCP command (Linux evdev event) the DUT receives
        contains extra information than just the AVRCP event, e.g. EV_REP
        report used to specify delay settings. Send the first command before
        the actual test starts to avoid dealing with them during test.

        The peer device name is required to monitor the event reception on the
        DUT. However, as the peer device itself already registered with the
        kernel as an udev input device. The AVRCP profile will register as an
        separate input device with the name pattern: name + (AVRCP), e.g.
        RASPI_AUDIO (AVRCP). Using 'AVRCP' as device name to help search for
        the device.

        @param device: the Bluetooth peer device

        @returns: True if the all AVRCP commands received by DUT, false
                  otherwise

        """
        device.SendMediaPlayerCommand('play')

        name = device.name
        device.name = 'AVRCP'

        result_pause = self.test_avrcp_event(device,
            device.SendMediaPlayerCommand, 'pause')
        result_play = self.test_avrcp_event(device,
            device.SendMediaPlayerCommand, 'play')
        result_stop = self.test_avrcp_event(device,
            device.SendMediaPlayerCommand, 'stop')
        result_next = self.test_avrcp_event(device,
            device.SendMediaPlayerCommand, 'next')
        result_previous = self.test_avrcp_event(device,
            device.SendMediaPlayerCommand, 'previous')

        device.name = name
        self.results = {'pause': result_pause, 'play': result_play,
                        'stop': result_stop, 'next': result_next,
                        'previous': result_previous}
        return all(self.results.values())


    @test_retry_and_log(False)
    def test_avrcp_media_info(self, device):
        """Test Case: Test AVRCP media info sent by DUT can be received by peer

        The test update all media information twice to prevent previous
        leftover data affect the current iteration of test. Then compare the
        expected results against the information received on the peer device.

        This test verifies media information including: playback status,
        length, title, artist, and album. Position of the media is not
        currently support as playerctl on the peer side cannot correctly
        retrieve such information.

        Length and position information are transmitted in the unit of
        microsecond. However, BlueZ process those time data in the resolution
        of millisecond. Discard microsecond detail when comparing those media
        information.

        @param device: the Bluetooth peer device

        @returns: True if the all AVRCP media info received by DUT, false
                  otherwise

        """
        # First round of updating media information to overwrite all leftovers.
        init_status = 'stopped'
        init_length = 20200414
        init_position = 8686868
        init_metadata = {'album': 'metadata_album_init',
                         'artist': 'metadata_artist_init',
                         'title': 'metadata_title_init'}
        self.bluetooth_facade.set_player_playback_status(init_status)
        self.bluetooth_facade.set_player_length(init_length)
        self.bluetooth_facade.set_player_position(init_position)
        self.bluetooth_facade.set_player_metadata(init_metadata)

        # Second round of updating for actual testing.
        expected_status = 'playing'
        expected_length = 68686868
        expected_position = 20200414
        expected_metadata = {'album': 'metadata_album_expected',
                             'artist': 'metadata_artist_expected',
                             'title': 'metadata_title_expected'}
        self.bluetooth_facade.set_player_playback_status(expected_status)
        self.bluetooth_facade.set_player_length(expected_length)
        self.bluetooth_facade.set_player_position(expected_position)
        self.bluetooth_facade.set_player_metadata(expected_metadata)

        received_media_info = device.GetMediaPlayerMediaInfo()
        logging.debug(received_media_info)

        try:
            actual_length = int(received_media_info.get('length'))
        except:
            actual_length = 0

        result_status = bool(expected_status ==
            received_media_info.get('status').lower())
        result_album = bool(expected_metadata['album'] ==
            received_media_info.get('album'))
        result_artist = bool(expected_metadata['artist'] ==
            received_media_info.get('artist'))
        result_title = bool(expected_metadata['title'] ==
            received_media_info.get('title'))
        # The AVRCP time information is in the unit of microseconds but with
        # milliseconds resolution. Convert both send and received length into
        # milliseconds for comparison.
        result_length = bool(expected_length / 1000 == actual_length / 1000)

        self.results = {'status': result_status, 'album': result_album,
                        'artist': result_artist, 'title': result_title,
                        'length': result_length}
        return all(self.results.values())
