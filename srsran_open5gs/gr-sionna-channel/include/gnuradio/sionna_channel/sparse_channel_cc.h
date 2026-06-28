#pragma once

#include <gnuradio/gr_complex.h>
#include <gnuradio/sionna_channel/api.h>
#include <gnuradio/sync_block.h>

#include <boost/shared_ptr.hpp>
#include <cstdint>
#include <vector>

namespace gr {
    namespace sionna_channel {
        class SIONNA_CHANNEL_API prepared_channel{
            public:
                virtual ~prepared_channel() = default;
        };

        using prepared_channel_sptr = boost::shared_ptr<prepared_channel>;

        class SIONNA_CHANNEL_API sparse_channel_cc : virtual public gr::sync_block{
            public:
                using sptr = boost::shared_ptr<sparse_channel_cc>;

                static sptr make(
                    const std::vector<gr_complex>& coefficients,
                    const std::vector<unsigned short>& delays);

                virtual prepared_channel_sptr prepare_channel(
                    std::uint64_t sequence,
                    std::uint64_t activate_at_sample,
                    const std::vector<gr_complex>& coefficients,
                    const std::vector<unsigned short>& delays) const = 0;

                virtual bool commit_channel(
                    const prepared_channel_sptr& prepared) = 0;

                virtual std::uint64_t sample_count() const = 0;                // Total number of samples this block has processed since it started.
                virtual std::uint64_t active_sequence() const = 0;             // Sequence number of the channel currently applied to the signal.
                virtual std::uint64_t pending_sequence() const = 0;            // Sequence number of a staged channel waiting to activate (max value = none pending).
                virtual std::uint64_t requested_activation_sample() const = 0; // The sample index at which the pending channel was asked to activate.
                virtual std::uint64_t actual_activation_sample() const = 0;    // The sample index at which the last channel actually activated.
                virtual std::uint64_t activation_time_ns() const = 0;          // Wall-clock time (nanoseconds) of the last activation, for latency measurement.
                virtual std::uint64_t latest_received_sequence() const = 0;    // Highest sequence number ever accepted, used to reject old/duplicate updates.
        };

        SIONNA_CHANNEL_API bool commit_both(
            const sparse_channel_cc::sptr& downlink,
            const prepared_channel_sptr& downlink_prepared,
            const sparse_channel_cc::sptr& uplink,
            const prepared_channel_sptr& uplink_prepared
        );
    }
}