#include "sparse_channel_cc_impl.h"

#include <gnuradio/gr_complex.h>

#include <cmath>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {

using gr::sionna_channel::sparse_channel_cc;
using gr::sionna_channel::sparse_channel_cc_impl;

void require(bool condition, const char* message)
{
    if (!condition) {
        throw std::runtime_error(message);
    }
}

void require_close(gr_complex actual, gr_complex expected)
{
    require(
        std::abs(actual - expected) < 1.0e-6F,
        "complex output did not match the expected value");
}

std::vector<gr_complex> run_chunk(
    sparse_channel_cc_impl& block,
    const std::vector<gr_complex>& input)
{
    std::vector<gr_complex> output(input.size());
    gr_vector_const_void_star inputs(1);
    gr_vector_void_star outputs(1);
    inputs[0] = input.data();
    outputs[0] = output.data();
    require(
        block.work(
            static_cast<int>(input.size()),
            inputs,
            outputs)
            == static_cast<int>(input.size()),
        "work returned an unexpected item count");
    return output;
}

template <typename Function>
void require_invalid(Function function, const char* message)
{
    try {
        function();
    } catch (const std::invalid_argument&) {
        return;
    }
    throw std::runtime_error(message);
}

} // namespace

int main()
{
    try {
        sparse_channel_cc_impl identity(
            {gr_complex(1.0F, 0.0F)}, {0}, 0);
        const auto identity_output = run_chunk(
            identity,
            {gr_complex(1.0F, 2.0F), gr_complex(-3.0F, 4.0F)});
        require_close(identity_output[0], gr_complex(1.0F, 2.0F));
        require_close(identity_output[1], gr_complex(-3.0F, 4.0F));

        sparse_channel_cc_impl delayed(
            {gr_complex(0.5F, -0.25F)}, {255}, 0);
        std::vector<gr_complex> first_chunk(100, gr_complex(0.0F, 0.0F));
        first_chunk[0] = gr_complex(1.0F, 0.0F);
        const auto first_output = run_chunk(delayed, first_chunk);
        for (const auto value : first_output) {
            require_close(value, gr_complex(0.0F, 0.0F));
        }
        const std::vector<gr_complex> second_chunk(
            200, gr_complex(0.0F, 0.0F));
        const auto second_output = run_chunk(delayed, second_chunk);
        require_close(second_output[155], gr_complex(0.5F, -0.25F));

        // set_channel is latest-wins from the next work() block
        sparse_channel_cc_impl streamed(
            {gr_complex(1.0F, 0.0F)}, {0}, 0);
        const auto before = run_chunk(
            streamed,
            std::vector<gr_complex>(4, gr_complex(1.0F, 0.0F)));
        for (const auto value : before) {
            require_close(value, gr_complex(1.0F, 0.0F));
        }
        streamed.set_channel({gr_complex(0.5F, 0.0F)}, {0}, 0.0);
        require(streamed.update_count() == 1, "update was not counted");
        require(streamed.tap_count() == 1, "tap count is wrong");
        const auto after = run_chunk(
            streamed,
            std::vector<gr_complex>(4, gr_complex(1.0F, 0.0F)));
        for (const auto value : after) {
            require_close(value, gr_complex(0.5F, 0.0F));
        }

        // history carries across a streamed CIR change (echo at delay 3)
        sparse_channel_cc_impl history(
            {gr_complex(1.0F, 0.0F)}, {0}, 0);
        run_chunk(
            history,
            {gr_complex(1.0F, 0.0F), gr_complex(0.0F, 0.0F),
             gr_complex(0.0F, 0.0F)});
        history.set_channel({gr_complex(0.5F, 0.0F)}, {3}, 0.0);
        const auto echo = run_chunk(history, {gr_complex(0.0F, 0.0F)});
        require_close(echo[0], gr_complex(0.5F, 0.0F));

        // per-symbol noise: sigma^2 power added on a silent input
        sparse_channel_cc_impl noisy(
            {gr_complex(1.0F, 0.0F)}, {0}, 0);
        noisy.set_channel({gr_complex(1.0F, 0.0F)}, {0}, 0.1);
        require(std::abs(noisy.noise_sigma() - 0.1) < 1e-6, "sigma not stored");
        const std::vector<gr_complex> silence(20000, gr_complex(0.0F, 0.0F));
        const auto noise_out = run_chunk(noisy, silence);
        double power = 0.0;
        for (const auto value : noise_out) {
            power += std::norm(value);
        }
        power /= noise_out.size();
        require(std::abs(power - 0.01) < 0.002, "noise power off target");

        // dense channels are accepted up to kMaxTaps (1024)
        std::vector<gr_complex> max_coefficients(
            1024, gr_complex(0.01F, 0.0F));
        std::vector<unsigned short> max_delays;
        for (unsigned short delay_value = 0;
             delay_value < 1024;
             ++delay_value) {
            max_delays.push_back(delay_value);
        }
        require(
            static_cast<bool>(sparse_channel_cc::make(
                max_coefficients, max_delays)),
            "1024 dense taps should be accepted");

        require_invalid(
            [] { sparse_channel_cc::make({}, {}); },
            "an empty channel should be rejected");
        require_invalid(
            [] {
                sparse_channel_cc::make(
                    {gr_complex(1.0F, 0.0F)}, {});
            },
            "mismatched arrays should be rejected");
        require_invalid(
            [] {
                sparse_channel_cc::make(
                    std::vector<gr_complex>(
                        1025, gr_complex(1.0F, 0.0F)),
                    std::vector<unsigned short>(1025, 0));
            },
            "1025 taps should be rejected");
        require_invalid(
            [&streamed] {
                streamed.set_channel(
                    {gr_complex(1.0F, 0.0F)}, {2000}, 0.0);
            },
            "a tap delay above the maximum should be rejected");
        require_invalid(
            [&streamed] {
                streamed.set_channel(
                    {gr_complex(1.0F, 0.0F)}, {0}, -1.0);
            },
            "a negative noise sigma should be rejected");

        std::cout << "qa_sparse_channel: PASS\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "qa_sparse_channel: FAIL: " << error.what() << '\n';
        return 1;
    }
}
