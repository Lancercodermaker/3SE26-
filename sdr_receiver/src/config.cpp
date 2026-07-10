#include "sdr_receiver/config.hpp"
#include <stdexcept>

namespace sdr_receiver::config {

RadioParams base_radio_params(Team team, Target target) {
    RadioParams p;
    p.ac = target == Target::Info ? AC_INFO : AC_JAM;
    if (team == Team::Red) {
        switch (target) {
            case Target::Info: p.freq = 433'200'000; p.gain = 40; p.rf_bw = 540'000; break;
            case Target::L1: p.freq = 432'200'000; p.gain = 22; p.rf_bw = 1'250'000; break;
            case Target::L2: p.freq = 432'500'000; p.gain = 22; p.rf_bw = 1'100'000; break;
            case Target::L3: p.freq = 432'800'000; p.gain = 25; p.rf_bw = 400'000; break;
        }
    } else {
        switch (target) {
            case Target::Info: p.freq = 433'920'000; p.gain = 40; p.rf_bw = 540'000; break;
            case Target::L1: p.freq = 434'920'000; p.gain = 22; p.rf_bw = 1'250'000; break;
            case Target::L2: p.freq = 434'620'000; p.gain = 22; p.rf_bw = 1'100'000; break;
            case Target::L3: p.freq = 434'320'000; p.gain = 25; p.rf_bw = 400'000; break;
        }
    }
    p.base_freq = p.freq;
    p.rx_lo = p.freq;
    p.gain_floor = p.gain;
    return p;
}

FilterParams base_filter_params(Target target) {
    FilterParams f;
    f.smooth_frac = 0.34;
    f.trend_bits = 16;
    f.max_ac_errors = 2;
    switch (target) {
        case Target::Info:
            f.kind = FilterKind::AsymFft;
            f.pass_low = -263'000.0; f.pass_high = 315'000.0;
            f.stop_low = -296'000.0; f.stop_high = 405'000.0;
            break;
        case Target::L1:
            f.kind = FilterKind::SymFft; f.cutoff = 620'000.0; f.transition = 90'000.0; break;
        case Target::L2:
            f.kind = FilterKind::SymFft; f.cutoff = 560'000.0; f.transition = 80'000.0; break;
        case Target::L3:
            f.kind = FilterKind::SymFft; f.cutoff = 220'000.0; f.transition = 60'000.0; f.smooth_frac = 0.38; break;
    }
    return f;
}

FilterParams info_l3_rescue_filter_params() {
    FilterParams f;
    f.kind = FilterKind::AsymFft;
    f.pass_low = -263'000.0; f.pass_high = 315'000.0;
    f.stop_low = -286'000.0; f.stop_high = 390'000.0;
    f.smooth_frac = 0.34; f.trend_bits = 16; f.max_ac_errors = 3;
    return f;
}

FilterParams info_l2_rescue_filter_params() {
    FilterParams f;
    f.kind = FilterKind::AsymFft;
    f.pass_low = -248'000.0; f.pass_high = 315'000.0;
    f.stop_low = -276'000.0; f.stop_high = 405'000.0;
    f.smooth_frac = 0.34; f.trend_bits = 16; f.max_ac_errors = 3;
    return f;
}

ReceiverState make_default_state() {
    ReceiverState s;
    s.manual_rx_gains[Target::Info] = 24;
    s.manual_rx_gains[Target::L1] = 22;
    s.manual_rx_gains[Target::L2] = 22;
    s.manual_rx_gains[Target::L3] = 25;
    s.jam_keys[Target::L1] = "---";
    s.jam_keys[Target::L2] = "---";
    s.jam_keys[Target::L3] = "---";
    s.jam_keys_cnt[Target::L1] = 0;
    s.jam_keys_cnt[Target::L2] = 0;
    s.jam_keys_cnt[Target::L3] = 0;
    s.cal.dwell_sec = CAL_DWELL_SEC;
    s.cal.validate_top_k = CAL_TOP_K;
    s.cal.validate_rounds = CAL_VALIDATE_ROUNDS;
    return s;
}

DataModel make_default_data_model() {
    DataModel d;
    for (auto k : {"H1", "E2", "I3", "I4", "A6", "S7"}) d.pos[k] = {0, 0};
    for (auto k : {"H1", "E2", "I3", "I4", "S7"}) d.hp[k] = 0;
    for (auto k : {"H1", "I3", "I4", "A6", "S7"}) d.ammo[k] = 0;
    for (auto k : {"H1", "E2", "I3", "I4", "S7"}) d.buff[k] = {0, 0, 0, 0, 0};
    return d;
}

} // namespace sdr_receiver::config
