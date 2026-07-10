#include "sdr_receiver/receiver_app.hpp"
#include "sdr_receiver/config.hpp"
#include <chrono>
#include <iostream>
#include <thread>

namespace sdr_receiver {

ReceiverApp::ReceiverApp(std::unique_ptr<ISdrDevice> sdr)
    : state_(config::make_default_state()),
      data_(config::make_default_data_model()),
      sdr_(std::move(sdr)),
      sdr_controller_(tune_, state_, data_),
      calibration_(tune_, state_, sdr_controller_),
      keyboard_(tune_, sdr_controller_, calibration_),
      demodulator_(tune_, state_, sdr_controller_) {}

int ReceiverApp::run() {
    keyboard_.init_input_terminal();
    terminal_ui_.render(tune_, state_, data_, sdr_controller_.get_effective_radio_params(), true);
    bool running = true;
    while (running) {
        const double t0 = now_sec();
        running = keyboard_.handle_keyboard();
        const RadioParams p = sdr_controller_.apply_sdr_config(*sdr_);
        const double rx0 = now_sec();
        auto rx = sdr_->receive(config::RX_BUFFER_SIZE);
        state_.stats.rx_ms = (now_sec() - rx0) * 1000.0;
        const double d0 = now_sec();
        demodulator_.fast_demod(rx, p.ac);
        state_.stats.demod_ms = (now_sec() - d0) * 1000.0;
        calibration_.update_calibration();
        calibration_.maybe_failover_cal_profile();
        state_.stats.loop_ms = (now_sec() - t0) * 1000.0;

        terminal_ui_.render(tune_, state_, data_, p);
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    keyboard_.restore_input_terminal();
    return 0;
}

} // namespace sdr_receiver
