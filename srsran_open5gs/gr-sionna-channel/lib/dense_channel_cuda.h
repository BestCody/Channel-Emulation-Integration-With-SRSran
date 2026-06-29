#pragma once

#include <cstddef>

namespace gr {
namespace sionna_channel {

// True when a usable CUDA device is present at runtime.
bool cuda_available();

// Dense FIR on the GPU: out[j] = sum_t coeff[t] * base[prefix + j - delay[t]].
// Complex arrays are interleaved float (re, im). base must hold prefix+count
// complex samples; out holds count complex samples.
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
