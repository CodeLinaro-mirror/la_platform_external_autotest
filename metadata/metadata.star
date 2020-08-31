# Copyright 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

load("@proto//chromiumos/config/api/test/metadata/v1/metadata.proto",
    metadata_pb = "chromiumos.config.api.test.metadata.v1"
)

load("//metadata/tests.star", "TESTS")

METADATA = metadata_pb.RemoteTestDriver(
    name = "remoteTestDrivers/tauto",
    # TODO: populate image and command.
    image = metadata_pb.BuildArtifact(
        relative_path = ""
    ),
    command = "",
    tests = TESTS,
)
