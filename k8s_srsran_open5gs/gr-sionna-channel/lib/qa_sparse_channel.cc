#include "sparse_channel_cc_impl.h"

#include <gnuradio/gr_complex.h>

#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <vector>

namespace {

using gr::sionna_channel::commit_both;
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
            {gr_complex(1.0F, 0.0F)},
            {0});
        const auto identity_output = run_chunk(
            identity,
            {gr_complex(1.0F, 2.0F), gr_complex(-3.0F, 4.0F)});
        require_close(identity_output[0], gr_complex(1.0F, 2.0F));
        require_close(identity_output[1], gr_complex(-3.0F, 4.0F));

        sparse_channel_cc_impl delayed(
            {gr_complex(0.5F, -0.25F)},
            {255});
        std::vector<gr_complex> first_chunk(100, gr_complex(0.0F, 0.0F));
        first_chunk[0] = gr_complex(1.0F, 0.0F);
        const auto first_output = run_chunk(delayed, first_chunk);
        for (const auto value : first_output) {
            require_close(value, gr_complex(0.0F, 0.0F));
        }
        const std::vector<gr_complex> second_chunk(
            200,
            gr_complex(0.0F, 0.0F));
        const auto second_output = run_chunk(delayed, second_chunk);
        require_close(second_output[155], gr_complex(0.5F, -0.25F));

        sparse_channel_cc_impl live(
            {gr_complex(1.0F, 0.0F)},
            {0});
        auto attenuation = live.prepare_channel(
            1,
            2,
            {gr_complex(0.5F, 0.0F)},
            {0});
        require(live.commit_channel(attenuation), "single commit failed");
        const auto live_output = run_chunk(
            live,
            std::vector<gr_complex>(4, gr_complex(1.0F, 0.0F)));
        require_close(live_output[0], gr_complex(1.0F, 0.0F));
        require_close(live_output[1], gr_complex(1.0F, 0.0F));
        require_close(live_output[2], gr_complex(0.5F, 0.0F));
        require_close(live_output[3], gr_complex(0.5F, 0.0F));
        require(live.active_sequence() == 1, "sequence did not activate");
        require(live.actual_activation_sample() == 2, "activation was late");

        sparse_channel_cc_impl history(
            {gr_complex(1.0F, 0.0F)},
            {0});
        run_chunk(
            history,
            {gr_complex(1.0F, 0.0F), gr_complex(0.0F, 0.0F),
             gr_complex(0.0F, 0.0F)});
        auto echo = history.prepare_channel(
            1,
            3,
            {gr_complex(0.5F, 0.0F)},
            {3});
        require(history.commit_channel(echo), "history commit failed");
        const auto history_output = run_chunk(
            history,
            {gr_complex(0.0F, 0.0F)});
        require_close(history_output[0], gr_complex(0.5F, 0.0F));

        auto downlink = sparse_channel_cc::make(
            {gr_complex(1.0F, 0.0F)}, {0});
        auto uplink = sparse_channel_cc::make(
            {gr_complex(1.0F, 0.0F)}, {0});
        auto down_prepared = downlink->prepare_channel(
            1, 100, {gr_complex(0.92F, 0.0F)}, {0});
        auto up_prepared = uplink->prepare_channel(
            1, 100, {gr_complex(0.92F, 0.0F)}, {0});
        require(
            commit_both(downlink, down_prepared, uplink, up_prepared),
            "two-block transaction failed");
        require(downlink->pending_sequence() == 1, "downlink not pending");
        require(uplink->pending_sequence() == 1, "uplink not pending");

        auto wrong_down = sparse_channel_cc::make(
            {gr_complex(1.0F, 0.0F)}, {0});
        auto wrong_up = sparse_channel_cc::make(
            {gr_complex(1.0F, 0.0F)}, {0});
        auto wrong_down_prepared = wrong_down->prepare_channel(
            1, 100, {gr_complex(0.5F, 0.0F)}, {0});
        auto wrong_up_prepared = wrong_up->prepare_channel(
            1, 100, {gr_complex(0.5F, 0.0F)}, {0});
        require(
            !commit_both(
                wrong_down,
                wrong_up_prepared,
                wrong_up,
                wrong_down_prepared),
            "ownership mismatch was accepted");
        require(
            wrong_down->pending_sequence() ==
                std::numeric_limits<std::uint64_t>::max(),
            "failed transaction changed downlink");
        require(
            wrong_up->pending_sequence() ==
                std::numeric_limits<std::uint64_t>::max(),
            "failed transaction changed uplink");

        auto stale_down = sparse_channel_cc::make(
            {gr_complex(1.0F, 0.0F)}, {0});
        auto stale_up = sparse_channel_cc::make(
            {gr_complex(1.0F, 0.0F)}, {0});
        auto stale_down_prepared = stale_down->prepare_channel(
            1, 100, {gr_complex(0.5F, 0.0F)}, {0});
        auto stale_up_prepared = stale_up->prepare_channel(
            1, 100, {gr_complex(0.5F, 0.0F)}, {0});
        auto newer_down = stale_down->prepare_channel(
            2, 200, {gr_complex(0.25F, 0.0F)}, {0});
        require(stale_down->commit_channel(newer_down), "setup commit failed");
        require(
            !commit_both(
                stale_down,
                stale_down_prepared,
                stale_up,
                stale_up_prepared),
            "stale generation was accepted");
        require(
            stale_up->pending_sequence() ==
                std::numeric_limits<std::uint64_t>::max(),
            "failed stale transaction changed the other block");

        auto preparation_down = sparse_channel_cc::make(
            {gr_complex(1.0F, 0.0F)}, {0});
        auto preparation_up = sparse_channel_cc::make(
            {gr_complex(1.0F, 0.0F)}, {0});
        auto prepared_only = preparation_down->prepare_channel(
            1, 100, {gr_complex(0.5F, 0.0F)}, {0});
        (void)prepared_only;
        require_invalid(
            [&preparation_up] {
                preparation_up->prepare_channel(
                    1,
                    100,
                    std::vector<gr_complex>(
                        49, gr_complex(0.01F, 0.0F)),
                    std::vector<unsigned short>(49, 0));
            },
            "invalid second preparation was accepted");
        require(
            preparation_down->pending_sequence() ==
                std::numeric_limits<std::uint64_t>::max(),
            "preparation changed downlink state");
        require(
            preparation_up->pending_sequence() ==
                std::numeric_limits<std::uint64_t>::max(),
            "failed preparation changed uplink state");

        std::vector<gr_complex> forty_eight_coefficients(
            48,
            gr_complex(0.01F, 0.0F));
        std::vector<unsigned short> forty_eight_delays;
        for (unsigned short delay_value = 0;
             delay_value < 48;
             ++delay_value) {
            forty_eight_delays.push_back(delay_value);
        }
        require(
            static_cast<bool>(sparse_channel_cc::make(
                forty_eight_coefficients,
                forty_eight_delays)),
            "48 taps should be accepted");

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
                        49, gr_complex(1.0F, 0.0F)),
                    std::vector<unsigned short>(49, 0));
            },
            "49 taps should be rejected");

        std::cout << "qa_sparse_channel: PASS\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "qa_sparse_channel: FAIL: " << error.what() << '\n';
        return 1;
    }
}
