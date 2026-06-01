from __future__ import annotations

import math
import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import numpy as np


@dataclass
class SensorConfig:
    red: int = 31
    ir: int = 31
    avg: int = 1
    rate: int = 100
    width: int = 411
    adc: int = 16384
    skip: int = 10
    debug: bool = False

    def command(self) -> str:
        return (
            f"CONFIG RED={self.red} IR={self.ir} AVG={self.avg} RATE={self.rate} "
            f"WIDTH={self.width} ADC={self.adc} SKIP={self.skip} DEBUG={1 if self.debug else 0}"
        )

    def clean(self) -> "SensorConfig":
        self.red = int(np.clip(self.red, 0, 255))
        self.ir = int(np.clip(self.ir, 0, 255))
        self.avg = int(self.avg if self.avg in (1, 2, 4, 8, 16, 32) else 1)
        self.rate = int(self.rate if self.rate in (50, 100, 200, 400, 800, 1000, 1600, 3200) else 100)
        self.width = int(self.width if self.width in (69, 118, 215, 411) else 411)
        self.adc = int(self.adc if self.adc in (2048, 4096, 8192, 16384) else 16384)
        self.skip = int(np.clip(self.skip, 0, 200))
        return self

@dataclass
class AnalysisConfig:
    bpm_min: int = 45
    bpm_max: int = 180
    peak_threshold_sd: float = 0.55
    detrend_seconds: float = 2.0
    smooth_seconds: float = 0.07
    ignore_initial_seconds: float = 1.0
    min_quality_to_accept: float = 45.0
    spo2_formula: str = "quad"
    spo2_custom_a: float = 94.845
    spo2_custom_b: float = 30.354
    spo2_custom_c: float = -45.060

@dataclass
class Metrics:
    n: int = 0
    hz: float = math.nan
    duration_s: float = math.nan
    bpm: float = math.nan
    bpm_peak: float = math.nan
    bpm_fft: float = math.nan
    bpm_autocorr: float = math.nan
    spo2: float = math.nan
    ratio_r: float = math.nan
    quality: float = 0.0
    quality_label: str = "sin datos"
    artifact_ir_pct: float = math.nan
    artifact_red_pct: float = math.nan
    pi_ir_pct: float = math.nan
    pi_red_pct: float = math.nan
    ac_ir: float = math.nan
    dc_ir: float = math.nan
    ac_red: float = math.nan
    dc_red: float = math.nan
    saturation_pct: float = math.nan
    contact_label: str = "-"
    polarity: str = "-"
    reason: str = ""
    peaks_count: int = 0

@dataclass
class CaptureState:
    mode: Literal["idle", "normal", "long", "scheduled", "temp", "temp_ajuste"] = "idle"
    capturing: bool = False
    finished: bool = False
    sensor_ready: bool = False
    capture_start_wall: float = 0.0
    requested_duration_s: float = 20.0
    crotal_id: str = "SIN_CROTAL"
    pulse_prev: str = ""
    pulse_final_pulsio: str = ""
    pulse_final_fonendo: str = ""
    measurement_condition: str = ""
    first_micro: Optional[int] = None
    t: list[float] = field(default_factory=list)
    red: list[float] = field(default_factory=list)
    ir: list[float] = field(default_factory=list)
    temp_c: list[float] = field(default_factory=list)
    temp_raw: list[float] = field(default_factory=list)
    config_label: str = ""
    valid_lines: int = 0
    discarded_lines: int = 0
    control_messages: int = 0
    rx_lines: int = 0
    rx_bytes: int = 0
    last_line: str = ""
    last_control: str = ""
    last_config_ack: str = "sin confirmar"
    last_config_line: str = ""
    base_name: str = ""
    session_id: str = ""
    raw_file: Optional[Path] = None
    raw_handle: Optional[object] = None
    raw_writer: Optional[csv.writer] = None
    processed_file: Optional[Path] = None
    plot_file: Optional[Path] = None
    screenshot_file: Optional[Path] = None
    config_file: Optional[Path] = None
    summary_file: Optional[Path] = None
    session_file: Optional[Path] = None
    metrics: Metrics = field(default_factory=Metrics)
    bpm_blocks: list[float] = field(default_factory=list)
    bpm_blocks_10s: list[float] = field(default_factory=list)
    blocks_file: Optional[Path] = None
    rolling_t: list[float] = field(default_factory=list)
    rolling_bpm: list[float] = field(default_factory=list)
    rolling_spo2: list[float] = field(default_factory=list)

