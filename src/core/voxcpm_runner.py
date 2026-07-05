"""Command-line bridge from SynthReel to the local VoxCPM clone engine."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import numpy as np


OUTPUT_PEAK_TARGET = 0.95
OUTPUT_MAX_GAIN = 4.0


def _env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "sim", "on"}


def _root_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _boost_output_volume(wav) -> np.ndarray:
    """Raise low VoxCPM speech to practical playback level without clipping."""

    if wav is None:
        return wav

    wav_np = np.asarray(wav, dtype=np.float32)
    if wav_np.size == 0:
        return wav_np

    peak = float(np.max(np.abs(wav_np)))
    if peak < 1e-6:
        return wav_np

    gain = min(OUTPUT_PEAK_TARGET / peak, OUTPUT_MAX_GAIN)
    if gain <= 1.0 and peak <= OUTPUT_PEAK_TARGET:
        return wav_np

    return np.clip(wav_np * gain, -OUTPUT_PEAK_TARGET, OUTPUT_PEAK_TARGET)


def _default_reference_wav(root: Path, clonador_dir: Path) -> Path:
    candidates = [
        root / "src" / "workspace" / "voice_refs" / "minha-voz_ref_45s.wav",
        root / "src" / "workspace" / "voice_refs" / "minha_voz_ref_45s.wav",
        clonador_dir / "examples" / "minha_voz_ref_30s.wav",
        clonador_dir / "examples" / "minha_voz.wav",
        clonador_dir / "examples" / "reference_speaker.wav",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def parse_args() -> argparse.Namespace:
    root = _root_dir()
    clonador_dir = root / "clonador-voz"
    parser = argparse.ArgumentParser(description="SynthReel VoxCPM local voice bridge")
    parser.add_argument("--text", required=True, help="Text to synthesize.")
    parser.add_argument("--output", required=True, help="Output audio path. WAV and MP3 are supported.")
    parser.add_argument(
        "--model-id",
        default=os.getenv("CLONADOR_MODEL_ID", "openbmb/VoxCPM2"),
        help="VoxCPM model id or local path.",
    )
    parser.add_argument(
        "--device",
        default=os.getenv("CLONADOR_DEVICE", "cpu"),
        help="Runtime device: auto, cpu, cuda, cuda:N.",
    )
    parser.add_argument(
        "--reference-wav",
        default=os.getenv("CLONADOR_REFERENCIA_WAV", str(_default_reference_wav(root, clonador_dir))),
        help="Reference WAV for local voice cloning.",
    )
    parser.add_argument(
        "--prompt-text",
        default=os.getenv("CLONADOR_PROMPT_TEXT", ""),
        help="Optional transcript for ultimate cloning mode.",
    )
    parser.add_argument(
        "--cfg-value",
        type=float,
        default=float(os.getenv("CLONADOR_CFG_VALUE", "2.0")),
        help="Classifier-free guidance value.",
    )
    parser.add_argument(
        "--inference-timesteps",
        type=int,
        default=int(os.getenv("CLONADOR_INFERENCE_TIMESTEPS", "10")),
        help="Diffusion steps. Higher is slower.",
    )
    parser.add_argument(
        "--max-len",
        type=int,
        default=int(os.getenv("CLONADOR_MAX_LEN", "4096")),
        help="Maximum generation length.",
    )
    parser.add_argument(
        "--no-normalize",
        action="store_true",
        help="Disable VoxCPM text normalization.",
    )
    parser.add_argument(
        "--denoise",
        action="store_true",
        default=_env_flag("CLONADOR_DENOISE", False),
        help="Enable reference denoise if the optional denoiser is available.",
    )
    parser.add_argument(
        "--no-denoise",
        action="store_true",
        help="Compatibility flag. Explicitly disables reference denoise.",
    )
    parser.add_argument(
        "--optimize",
        action="store_true",
        default=_env_flag("CLONADOR_OPTIMIZE", False),
        help="Enable VoxCPM torch.compile warmup. Slower startup, faster repeated calls.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = _root_dir()
    clonador_dir = root / "clonador-voz"

    if str(clonador_dir) not in sys.path:
        sys.path.insert(0, str(clonador_dir))
    src_dir = clonador_dir / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    import soundfile as sf
    from voxcpm.core import VoxCPM

    reference_wav = Path(args.reference_wav)
    if not reference_wav.exists():
        raise FileNotFoundError(f"Reference WAV not found: {reference_wav}")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    denoise = bool(args.denoise and not args.no_denoise)

    print(
        f"[SynthReel VoxCPM] Loading local clone model on device={args.device}...",
        file=sys.stderr,
    )
    model = VoxCPM.from_pretrained(
        hf_model_id=args.model_id,
        load_denoiser=denoise,
        optimize=args.optimize,
        device=args.device,
    )
    print("[SynthReel VoxCPM] Generating cloned narration...", file=sys.stderr)
    prompt_text = args.prompt_text.strip() or None
    prompt_wav_path = str(reference_wav) if prompt_text else None
    wav_np = model.generate(
        text=args.text,
        prompt_wav_path=prompt_wav_path,
        prompt_text=prompt_text,
        reference_wav_path=str(reference_wav),
        cfg_value=args.cfg_value,
        inference_timesteps=args.inference_timesteps,
        max_len=args.max_len,
        normalize=not args.no_normalize,
        denoise=denoise,
    )
    wav_np = _boost_output_volume(wav_np)
    _save_audio(sf, wav_np, model.tts_model.sample_rate, output_path)
    print(f"[SynthReel VoxCPM] Saved: {output_path}", file=sys.stderr)
    return 0


def _save_audio(sf_module, wav_np, sample_rate: int, output_path: Path) -> None:
    if output_path.suffix.lower() == ".wav":
        sf_module.write(str(output_path), wav_np, sample_rate)
        return

    temp_wav = output_path.with_name(f"{output_path.stem}_voxcpm_tmp.wav")
    sf_module.write(str(temp_wav), wav_np, sample_rate)
    try:
        _convert_wav_with_ffmpeg(temp_wav, output_path)
    finally:
        temp_wav.unlink(missing_ok=True)


def _convert_wav_with_ffmpeg(input_wav: Path, output_path: Path) -> None:
    ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg")
    output_suffix = output_path.suffix.lower()

    if output_suffix == ".mp3":
        args = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_wav),
            "-af",
            "loudnorm=I=-14:TP=-1.2:LRA=8",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(output_path),
        ]
    else:
        args = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_wav),
            str(output_path),
        ]

    subprocess.run(args, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    raise SystemExit(main())
