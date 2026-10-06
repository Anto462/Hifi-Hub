"""Cadena DSP opt-in del reproductor.

Al activar cualquier procesamiento, el signal path lo refleja al instante.

EQ: gráfico de 10 bandas ISO sobre el filtro `firequalizer` de ffmpeg (ganancia
en dB por punto de frecuencia, interpolado). Verificado que libmpv acepta la
cadena `lavfi=[firequalizer=gain_entry='...']`.

Crossfade: NO implementado deliberadamente — libmpv usa un único decodificador
y no puede solapar dos pistas; el gapless ya cubre la escucha de álbumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Bandas ISO de un EQ gráfico de 10 bandas (Hz).
EQ_FREQS = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
GAIN_LIMIT = 12.0  # dB
FREQ_MIN, FREQ_MAX = 20.0, 20000.0   # rango audible para las Fc editables


@dataclass
class Equalizer:
    gains: list[float] = field(default_factory=lambda: [0.0] * len(EQ_FREQS))
    enabled: bool = False
    # Frecuencias centrales editables por banda (semiparamétrico). Por defecto ISO.
    freqs: list[float] = field(default_factory=lambda: list(EQ_FREQS))

    def set_gains(self, gains: list[float]) -> None:
        if len(gains) != len(EQ_FREQS):
            raise ValueError(f"Se esperan {len(EQ_FREQS)} ganancias")
        self.gains = [max(-GAIN_LIMIT, min(GAIN_LIMIT, float(g))) for g in gains]

    def set_freqs(self, freqs: list[float] | None) -> None:
        """Fija las frecuencias centrales (None = volver a las ISO)."""
        if freqs is None:
            self.freqs = list(EQ_FREQS)
            return
        if len(freqs) != len(EQ_FREQS):
            raise ValueError(f"Se esperan {len(EQ_FREQS)} frecuencias")
        clamped = [max(FREQ_MIN, min(FREQ_MAX, float(f))) for f in freqs]
        # firequalizer exige puntos estrictamente crecientes.
        self.freqs = sorted(clamped)

    @property
    def is_flat(self) -> bool:
        return all(g == 0 for g in self.gains)

    @property
    def auto_preamp_db(self) -> float:
        """Atenuación necesaria para que el EQ no recorte (0 o negativa).

        Subir una banda +6 dB puede llevar la señal por encima de 0 dBFS y
        recortar digitalmente. Se compensa bajando el nivel exactamente esa misma
        cantidad.
        """
        if not self.enabled:
            return 0.0
        peak = max(self.gains) if self.gains else 0.0
        return -peak if peak > 0 else 0.0

    def af_fragment(self, solo: int | None = None) -> str:
        """Fragmento de filtro ffmpeg, para combinar con otras cadenas. Vacío si el EQ está apagado.

        Aísla una banda de forma que el resto se atenúa fuerte para poder afinarla de
        oído.
        """
        if not self.enabled:
            return ""
        gains = list(self.gains)
        if solo is not None and 0 <= solo < len(gains):
            gains = [gains[i] if i == solo else -GAIN_LIMIT for i in range(len(gains))]
        entries = ";".join(f"entry({f:g},{g:g})" for f, g in zip(self.freqs, gains))
        return f"firequalizer=gain_entry='{entries}'"

    def af_string(self) -> str:
        """Cadena de filtros mpv completa (con envoltura lavfi)."""
        frag = self.af_fragment()
        return f"lavfi=[{frag}]" if frag else ""

    def state(self) -> dict:
        return {
            "enabled": self.enabled,
            "gains": list(self.gains),
            "freqs": list(self.freqs),
            "default_freqs": list(EQ_FREQS),
            "auto_preamp": self.auto_preamp_db,
        }
