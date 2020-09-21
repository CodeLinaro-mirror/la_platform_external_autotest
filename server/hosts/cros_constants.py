# Copyright (c) 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# Device is not fixable due issues with hardware and has to be replaced
DEVICE_STATE_NEEDS_REPLACEMENT = 'needs_replacement'
# Device required manual attention to be fixed
DEVICE_STATE_NEEDS_MANUAL_REPAIR = 'needs_manual_repair'

# Default timeout for all verifiers
# In order to avoid altering the behavior of the verifiers, set an extremely
# large timeout. The number must be large, but still fit in a C int.
VERIFY_TIMEOUT_SEC = 2**30

# Default timeout for all repair actions
# Default timeout for all verifiers
# In order to avoid altering the behavior of the repair actions,
# set an extremely large timeout. The number mut be large,
# but still fit in a C int.
REPAIR_TIMEOUT_SEC = 2**30
