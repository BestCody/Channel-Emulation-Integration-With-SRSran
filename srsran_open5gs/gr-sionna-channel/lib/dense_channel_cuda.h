#pragma once

#include <cstddef>

namespace gr {
namespace sionna_channel {

// True when a usable CUDA device is present
bool cuda_available();

// Dense GPU FIR uses interleaved complex arrays
void cuda_dense_fir(
    const int* delays,
    const float* coeffs,
    int ntaps,
    const float* base,
    int prefix,
    int count,
    float* out);

}
}
