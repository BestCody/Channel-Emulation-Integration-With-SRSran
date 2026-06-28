#include "sparse_channel_cc_impl.h"

#include <gnuradio/io_signature.h>
#include <gnuradio/sptr_magic.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <stdexcept>
#include <system_error>
#include <utility>

namespace gr {
namespace sionna_channel {

namespace {

std::atomic<std::uint64_t> g_next_block_id{1};

std::uint64_t monotonic_time_ns()
{
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch())
            .count());
}

}

sparse_channel_cc::sptr sparse_channel_cc::make(
    const std::vector<gr_complex>& coefficients,
    const std::vector<unsigned short>& delays)
{
    return gnuradio::get_initial_sptr(
        new sparse_channel_cc_impl(coefficients, delays));
}

std::uint64_t sparse_channel_cc_impl::next_block_id()
{
    return g_next_block_id.fetch_add(1, std::memory_order_relaxed);
}

std::shared_ptr<const sparse_channel_cc_impl::channel_model>
sparse_channel_cc_impl::build_model(
    std::uint64_t sequence,
    std::uint64_t activate_at_sample,
    const std::vector<gr_complex>& coefficients,
    const std::vector<unsigned short>& delays)
{
    if (coefficients.size() != delays.size()) {
        throw std::invalid_argument(
            "coefficient and delay counts must match");
    }
    if (coefficients.empty() || coefficients.size() > kMaxTaps) {
        throw std::invalid_argument(
            "channel must contain between one and 48 taps");
    }

    auto model = std::make_shared<channel_model>();
    model->sequence = sequence;
    model->activate_at_sample = activate_at_sample;
    model->taps.reserve(coefficients.size());

    for (std::size_t index = 0; index < coefficients.size(); ++index) {
        if (delays[index] > kMaxDelay) {
            throw std::invalid_argument(
                "tap delay exceeds the maximum of 255 samples");
        }
        if (!std::isfinite(coefficients[index].real()) ||
            !std::isfinite(coefficients[index].imag())) {
            throw std::invalid_argument(
                "tap coefficients must be finite");
        }
        model->taps.push_back(tap{delays[index], coefficients[index]});
    }
    return model;
}

sparse_channel_cc_impl::sparse_channel_cc_impl(
    const std::vector<gr_complex>& coefficients,
    const std::vector<unsigned short>& delays)
    : gr::sync_block(
          "sparse_channel_cc",
          gr::io_signature::make(1, 1, sizeof(gr_complex)),
          gr::io_signature::make(1, 1, sizeof(gr_complex))),
      d_block_id(next_block_id()),
      d_active(build_model(0, 0, coefficients, delays))
{
}

prepared_channel_sptr sparse_channel_cc_impl::prepare_channel(
    std::uint64_t sequence,
    std::uint64_t activate_at_sample,
    const std::vector<gr_complex>& coefficients,
    const std::vector<unsigned short>& delays) const
{
    auto prepared = boost::shared_ptr<prepared_channel_impl>(
        new prepared_channel_impl());
    prepared->owner_id = d_block_id;
    prepared->model = build_model(
        sequence,
        activate_at_sample,
        coefficients,
        delays);

    std::lock_guard<std::mutex> lock(d_pending_mutex);
    if (sequence <=
        d_latest_received_sequence.load(std::memory_order_relaxed)) {
        throw std::invalid_argument(
            "channel sequence must be newer than the latest update");
    }
    prepared->expected_generation = d_pending_generation;
    return prepared;
}

bool sparse_channel_cc_impl::validate_prepared_locked(
    const boost::shared_ptr<prepared_channel_impl>& prepared) const
{
    return prepared && !prepared->consumed && prepared->model &&
        prepared->owner_id == d_block_id &&
        prepared->expected_generation == d_pending_generation &&
        prepared->model->sequence >
            d_latest_received_sequence.load(std::memory_order_relaxed);
}

void sparse_channel_cc_impl::publish_prepared_locked(
    const boost::shared_ptr<prepared_channel_impl>& prepared)
{
    d_pending = prepared->model;
    d_pending_sequence.store(
        prepared->model->sequence,
        std::memory_order_relaxed);
    d_requested_activation_sample.store(
        prepared->model->activate_at_sample,
        std::memory_order_relaxed);
    d_latest_received_sequence.store(
        prepared->model->sequence,
        std::memory_order_relaxed);
    ++d_pending_generation;
    prepared->consumed = true;
    d_has_pending.store(true, std::memory_order_release);
}

bool sparse_channel_cc_impl::commit_channel(
    const prepared_channel_sptr& prepared)
{
    auto concrete =
        boost::dynamic_pointer_cast<prepared_channel_impl>(prepared);
    try {
        std::unique_lock<std::mutex> lock(d_pending_mutex);
        if (!validate_prepared_locked(concrete)) {
            return false;
        }
        publish_prepared_locked(concrete);
        return true;
    } catch (const std::system_error&) {
        return false;
    }
}

bool commit_both(
    const sparse_channel_cc::sptr& downlink,
    const prepared_channel_sptr& downlink_prepared,
    const sparse_channel_cc::sptr& uplink,
    const prepared_channel_sptr& uplink_prepared)
{
    auto downlink_impl =
        boost::dynamic_pointer_cast<sparse_channel_cc_impl>(downlink);
    auto uplink_impl =
        boost::dynamic_pointer_cast<sparse_channel_cc_impl>(uplink);
    auto downlink_model =
        boost::dynamic_pointer_cast<
            sparse_channel_cc_impl::prepared_channel_impl>(
            downlink_prepared);
    auto uplink_model =
        boost::dynamic_pointer_cast<
            sparse_channel_cc_impl::prepared_channel_impl>(
            uplink_prepared);

    if (!downlink_impl || !uplink_impl ||
        downlink_impl.get() == uplink_impl.get() ||
        !downlink_model || !uplink_model ||
        !downlink_model->model || !uplink_model->model ||
        downlink_model->model->sequence != uplink_model->model->sequence ||
        downlink_model->model->activate_at_sample !=
            uplink_model->model->activate_at_sample) {
        return false;
    }

    sparse_channel_cc_impl* first = downlink_impl.get();
    sparse_channel_cc_impl* second = uplink_impl.get();
    if (second->d_block_id < first->d_block_id) {
        std::swap(first, second);
    }

    try {
        std::unique_lock<std::mutex> first_lock(first->d_pending_mutex);
        std::unique_lock<std::mutex> second_lock(second->d_pending_mutex);

        if (!downlink_impl->validate_prepared_locked(downlink_model) ||
            !uplink_impl->validate_prepared_locked(uplink_model)) {
            return false;
        }

        downlink_impl->publish_prepared_locked(downlink_model);
        uplink_impl->publish_prepared_locked(uplink_model);
        return true;
    } catch (const std::system_error&) {
        return false;
    }
}

void sparse_channel_cc_impl::activate_pending(
    std::uint64_t absolute_sample)
{
    if (!d_has_pending.load(std::memory_order_acquire) ||
        absolute_sample <
            d_requested_activation_sample.load(
                std::memory_order_relaxed)) {
        return;
    }

    std::lock_guard<std::mutex> lock(d_pending_mutex);
    if (!d_pending ||
        absolute_sample < d_pending->activate_at_sample) {
        return;
    }

    d_active = d_pending;
    d_pending = nullptr;
    d_active_sequence.store(
        d_active->sequence,
        std::memory_order_relaxed);
    d_pending_sequence.store(
        std::numeric_limits<std::uint64_t>::max(),
        std::memory_order_relaxed);
    d_actual_activation_sample.store(
        absolute_sample,
        std::memory_order_relaxed);
    d_activation_time_ns.store(
        monotonic_time_ns(),
        std::memory_order_relaxed);
    d_pending_generation++;
    d_has_pending.store(false, std::memory_order_release);
}

std::uint64_t sparse_channel_cc_impl::sample_count() const
{
    return d_sample_count.load(std::memory_order_relaxed);
}

std::uint64_t sparse_channel_cc_impl::active_sequence() const
{
    return d_active_sequence.load(std::memory_order_relaxed);
}

std::uint64_t sparse_channel_cc_impl::pending_sequence() const
{
    return d_pending_sequence.load(std::memory_order_relaxed);
}

std::uint64_t
sparse_channel_cc_impl::requested_activation_sample() const
{
    return d_requested_activation_sample.load(
        std::memory_order_relaxed);
}

std::uint64_t sparse_channel_cc_impl::actual_activation_sample() const
{
    return d_actual_activation_sample.load(
        std::memory_order_relaxed);
}

std::uint64_t sparse_channel_cc_impl::activation_time_ns() const
{
    return d_activation_time_ns.load(std::memory_order_relaxed);
}

std::uint64_t sparse_channel_cc_impl::latest_received_sequence() const
{
    return d_latest_received_sequence.load(
        std::memory_order_relaxed);
}

int sparse_channel_cc_impl::work(
    int noutput_items,
    gr_vector_const_void_star& input_items,
    gr_vector_void_star& output_items)
{
    const auto* input = static_cast<const gr_complex*>(input_items[0]);
    auto* output = static_cast<gr_complex*>(output_items[0]);
    auto absolute_sample = d_sample_count.load(std::memory_order_relaxed);

    for (int item = 0; item < noutput_items; ++item, ++absolute_sample){
        activate_pending(absolute_sample);
        d_history[d_write_index] = input[item];

        gr_complex result(0.0F, 0.0F);
        for (const auto& current_tap : d_active->taps) {
            const auto read_index =
                (d_write_index + kHistorySize - current_tap.delay)
                & kHistoryMask;
            result += current_tap.coefficient * d_history[read_index];
        }

        output[item] = result;
        d_write_index = (d_write_index + 1) & kHistoryMask;
    }

    d_sample_count.store(absolute_sample, std::memory_order_relaxed);
    return noutput_items;
}

}
}