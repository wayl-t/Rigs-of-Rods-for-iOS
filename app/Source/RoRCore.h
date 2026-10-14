#pragma once

#include <stdint.h>

// Small C-facing surface of the native core. Everything RoR-related grows from
// here: right now it proves that C++20 compiles and runs on the device, and it
// carries the first piece of the real node-beam model.
#ifdef __cplusplus
extern "C" {
#endif

/// Bytes iOS is currently willing to let this process use. This is the ceiling
/// that decides which graphics settings fit, and it depends on how the app was
/// signed (free Apple ID vs paid account), not on the device alone.
uint64_t rorcore_available_memory(void);

/// Human-readable version of the native core.
const char *rorcore_version(void);

/// One iteration of the node-beam force, the way Rigs of Rods models it.
/// Returns the scalar force along the beam: positive = pull together, negative = push apart.
double rorcore_beam_force(double rest_len, double cur_len, double stiffness, double damping,
                          double rel_velocity_along_beam);

/// Returns 0 when the core behaves as expected. Used by the UI as a smoke test.
int rorcore_self_test(void);

#ifdef __cplusplus
}
#endif
