#pragma once

#include <gnuradio/sionna_channel/sparse_channel_cc.h>

#include <array>
#include <atomic>
#include <cstdint>
#include <limits>
#include <memory>
#include <mutex>
#include <vector>

namespace gr {
    namespace sionna_channel {

        class sparse_channel_cc_impl final : public sparse_channel_cc{
            public:
                sparse_channel_cc_impl(
                    const std::vector<gr_complex>& coefficients, // complex weights
                    const std::vector<unsigned short>& delays);  // sample offsets

                prepared_channel_sptr prepare_channel(
                    std::uint64_t sequence,                                    // monotonically increasing update id; orders updates and rejects stale/duplicate ones
                    std::uint64_t activate_at_sample,                          // absolute sample index (from block start) at which the new channel should take effect
                    const std::vector<gr_complex>& coefficients,               // complex tap weights of the new channel
                    const std::vector<unsigned short>& delays) const override; // per-tap delays (samples) for the new channel, paired 1:1 with coefficients

                bool commit_channel(
                    const prepared_channel_sptr& prepared) override;

                std::uint64_t sample_count() const override;                // Total number of samples this block has processed since it started.
                std::uint64_t active_sequence() const override;             // Sequence number of the channel currently applied to the signal.
                std::uint64_t pending_sequence() const override;            // Sequence number of a staged channel waiting to activate (max value = none pending).
                std::uint64_t requested_activation_sample() const override; // The sample index at which the pending channel was asked to activate.
                std::uint64_t actual_activation_sample() const override;    // The sample index at which the last channel actually activated.
                std::uint64_t activation_time_ns() const override;          // Wall-clock time (nanoseconds) of the last activation, for latency measurement.
                std::uint64_t latest_received_sequence() const override;    // Highest sequence number ever accepted, used to reject old/duplicate updates.

                int work(
                    int noutput_items,                       // how many output samples the scheduler wants this call
                    gr_vector_const_void_star& input_items,  // list<const void*>, one per input port; input_items[0] = read-only incoming samples
                    gr_vector_void_star& output_items)       // list<void*>, one per output port; output_items[0] = buffer to write results into
                    override;

            private:
                static constexpr std::size_t kMaxTaps = 48;                  // most taps a channel may have (limits work() loop length)
                static constexpr std::size_t kMaxDelay = 255;                // largest delay a tap may use, in samples (oldest sample reachable)
                static constexpr std::size_t kHistorySize = kMaxDelay + 1;   // ring buffer length = 256, must hold delay 0..255; power of two
                static constexpr std::size_t kHistoryMask = kHistorySize - 1; // = 255; lets index wrap with `& kHistoryMask` instead of `% 256`

                struct tap {
                    std::uint16_t delay;
                    gr_complex coefficient;
                };

                struct channel_model {
                    std::uint64_t sequence;
                    std::uint64_t activate_at_sample;
                    std::vector<tap> taps;
                };

                class prepared_channel_impl final : public prepared_channel{
                    public:
                        std::uint64_t owner_id;
                        std::uint64_t expected_generation;
                        std::shared_ptr<const channel_model> model;
                        bool consumed = false;
                };

                static std::shared_ptr<const channel_model> build_model(
                    std::uint64_t sequence,
                    std::uint64_t activate_at_sample,
                    const std::vector<gr_complex>& coefficients,
                    const std::vector<unsigned short>& delays);
                static std::uint64_t next_block_id();

                bool validate_prepared_locked(
                    const boost::shared_ptr<prepared_channel_impl>& prepared) const;
                void publish_prepared_locked(
                    const boost::shared_ptr<prepared_channel_impl>& prepared);
                void activate_pending(std::uint64_t absolute_sample);

                friend bool commit_both(
                    const sparse_channel_cc::sptr& downlink,
                    const prepared_channel_sptr& downlink_prepared,
                    const sparse_channel_cc::sptr& uplink,
                    const prepared_channel_sptr& uplink_prepared);

                std::array<gr_complex, kHistorySize> d_history{};
                std::size_t d_write_index = 0;
                const std::uint64_t d_block_id;
                std::shared_ptr<const channel_model> d_active;
                std::shared_ptr<const channel_model> d_pending;
                mutable std::mutex d_pending_mutex;
                std::uint64_t d_pending_generation = 0;
                std::atomic<bool> d_has_pending{false};
                std::atomic<std::uint64_t> d_sample_count{0};
                std::atomic<std::uint64_t> d_active_sequence{0};
                std::atomic<std::uint64_t> d_pending_sequence{std::numeric_limits<std::uint64_t>::max()};
                std::atomic<std::uint64_t> d_requested_activation_sample{0};
                std::atomic<std::uint64_t> d_actual_activation_sample{0};
                std::atomic<std::uint64_t> d_activation_time_ns{0};
                std::atomic<std::uint64_t> d_latest_received_sequence{0};
        };
    }
}