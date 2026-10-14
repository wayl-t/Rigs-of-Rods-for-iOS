#include "RoRCore.h"

#include <os/proc.h>

#include <cmath>
#include <string>

// Compiled as C++20 for arm64 iOS, the same language level the Rigs of Rods
// sources expect. Kept dependency-free on purpose: this must build anywhere.

namespace {

const char *kVersion = "rorcore 0.1.0 (pipeline smoke test)";

// A beam in RoR is a spring between two nodes with a rest length. The force is
// proportional to how far it is stretched, minus damping against how fast it is
// stretching. This mirrors the model, in one iteration form.
double BeamForce(double rest_len, double cur_len, double stiffness, double damping,
                 double rel_velocity_along_beam) {
  const double stretch = cur_len - rest_len;
  return -stiffness * stretch - damping * rel_velocity_along_beam;
}

}  // namespace

extern "C" uint64_t rorcore_available_memory(void) {
  return static_cast<uint64_t>(os_proc_available_memory());
}

extern "C" const char *rorcore_version(void) { return kVersion; }

extern "C" double rorcore_beam_force(double rest_len, double cur_len, double stiffness,
                                     double damping, double rel_velocity_along_beam) {
  return BeamForce(rest_len, cur_len, stiffness, damping, rel_velocity_along_beam);
}

extern "C" int rorcore_self_test(void) {
  // A beam at rest pushes nothing.
  if (std::fabs(BeamForce(3.0, 3.0, 12000.0, 400.0, 0.0)) > 1e-9) return 1;

  // A compressed beam (shorter than rest) must push apart -> positive force.
  if (BeamForce(3.0, 2.0, 12000.0, 400.0, 0.0) <= 0.0) return 2;

  // A stretched beam must pull together -> negative force.
  if (BeamForce(3.0, 4.0, 12000.0, 400.0, 0.0) >= 0.0) return 3;

  // Damping must oppose the stretching velocity.
  if (BeamForce(3.0, 3.0, 12000.0, 400.0, 5.0) >= 0.0) return 4;

  return 0;
}
