from __future__ import annotations

from statistics import mean, pvariance

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.domain.models import Track, TrackFeaturesNorm, TrackFeaturesRaw
from backend.domain.settings import settings

try:
    import essentia.standard as es
except Exception:  # pragma: no cover
    es = None


def _safe_number(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def extract_features(path: str) -> dict:
    if es is None:
        raise RuntimeError("Essentia is not available in worker runtime.")

    loader = es.MonoLoader(filename=path, sampleRate=44100)
    audio = loader()
    # Tempo extraction on very long files can fail in Essentia internals.
    # Use a capped analysis window for BPM algorithms only.
    rhythm_audio = audio[: 44100 * 600] if len(audio) > 44100 * 600 else audio
    bpm = 0.0
    # BPM extraction fallback chain:
    # 1) RhythmExtractor2013 (best quality for most files)
    # 2) BeatTrackerDegara (robust fallback)
    # 3) Neutral default 0.0 if both fail
    try:
        rhythm = es.RhythmExtractor2013(method="multifeature")
        bpm, _, _, _, _ = rhythm(rhythm_audio)
    except Exception:
        try:
            if hasattr(es, "BeatTrackerDegara"):
                bpm, _ = es.BeatTrackerDegara()(rhythm_audio)
            else:
                bpm = 0.0
        except Exception:
            bpm = 0.0
    key_extractor = es.KeyExtractor()
    key, scale, key_strength = key_extractor(audio)
    loudness = es.Loudness()(audio)
    rms_alg = es.RMS() if hasattr(es, "RMS") else None
    spectral = es.SpectralCentroidTime()
    window = es.Windowing(type="hann")
    spectrum = es.Spectrum()
    rolloff_alg = es.RollOff() if hasattr(es, "RollOff") else None
    mfcc_alg = es.MFCC() if hasattr(es, "MFCC") else None
    onset_alg = es.OnsetDetection(method="hfc") if hasattr(es, "OnsetDetection") else None
    frame_gen = es.FrameGenerator(audio, frameSize=2048, hopSize=1024, startFromZero=True)
    centroids = []
    rolloffs = []
    mfcc_means = []
    onset_values = []
    rms_values = []
    spectral_flux_values = []
    zcr_values = []
    zcr_alg = es.ZeroCrossingRate() if hasattr(es, "ZeroCrossingRate") else None
    flux_alg = es.Flux() if hasattr(es, "Flux") else None
    for frame in frame_gen:
        spec = spectrum(window(frame))
        centroids.append(spectral(spec))
        if rolloff_alg:
            rolloffs.append(_safe_number(rolloff_alg(spec)))
        if mfcc_alg:
            _, mfcc = mfcc_alg(spec)
            if len(mfcc) > 0:
                mfcc_means.append(float(mean(mfcc)))
        if onset_alg:
            onset_values.append(_safe_number(onset_alg(spec, spec)))
        if rms_alg:
            rms_values.append(_safe_number(rms_alg(frame)))
        if zcr_alg:
            zcr_values.append(_safe_number(zcr_alg(frame)))
        if flux_alg:
            spectral_flux_values.append(_safe_number(flux_alg(spec)))

    centroid_mean = mean(centroids) if centroids else 0.0
    rolloff_mean = mean(rolloffs) if rolloffs else (centroid_mean * 1.35)
    mfcc_mean = mean(mfcc_means) if mfcc_means else (centroid_mean / 1000.0)
    mfcc_var = pvariance(mfcc_means) if len(mfcc_means) > 1 else 0.0

    danceability = None
    if hasattr(es, "Danceability"):
        try:
            danceability = _safe_number(es.Danceability()(audio))
        except Exception:
            danceability = None
    if danceability is None:
        danceability = min(1.0, max(0.0, _safe_number(bpm) / 180.0))

    onset_rate = 0.0
    if onset_values:
        onset_rate = min(1.0, max(0.0, mean(onset_values)))
    rms_mean = mean(rms_values) if rms_values else 0.0
    spectral_flux_mean = mean(spectral_flux_values) if spectral_flux_values else 0.0
    zcr_mean = mean(zcr_values) if zcr_values else 0.0

    dynamic_complexity = 0.0
    if hasattr(es, "DynamicComplexity"):
        try:
            dynamic_complexity, _ = es.DynamicComplexity()(audio)
        except Exception:
            dynamic_complexity = 0.0

    tf_embedding_mean = 0.0
    tf_embedding_var = 0.0
    if settings.essentia_tf_model_path and hasattr(es, "TensorflowInputMusiCNN") and hasattr(es, "TensorflowPredict"):
        try:
            tf_input = es.TensorflowInputMusiCNN()
            patches = tf_input(audio)
            predictor = es.TensorflowPredict(graphFilename=settings.essentia_tf_model_path)
            predictions = predictor(patches)
            # flatten nested predictions robustly
            flat_vals = []
            for row in predictions:
                if hasattr(row, "__iter__"):
                    flat_vals.extend([_safe_number(v) for v in row])
                else:
                    flat_vals.append(_safe_number(row))
            if flat_vals:
                tf_embedding_mean = mean(flat_vals)
                tf_embedding_var = pvariance(flat_vals) if len(flat_vals) > 1 else 0.0
        except Exception:
            tf_embedding_mean = 0.0
            tf_embedding_var = 0.0

    return {
        "bpm": _safe_number(bpm),
        "key": key,
        "scale": scale,
        "key_strength": _safe_number(key_strength),
        "loudness": _safe_number(loudness),
        "spectral_centroid": _safe_number(centroid_mean),
        "spectral_rolloff": _safe_number(rolloff_mean),
        "mfcc_mean": _safe_number(mfcc_mean),
        "mfcc_var": _safe_number(mfcc_var),
        "onset_rate": _safe_number(onset_rate),
        "spectral_flux": _safe_number(spectral_flux_mean),
        "zcr": _safe_number(zcr_mean),
        "dynamic_complexity": _safe_number(dynamic_complexity),
        "tf_embedding_mean": _safe_number(tf_embedding_mean),
        "tf_embedding_var": _safe_number(tf_embedding_var),
        # Energy proxy combines RMS + spectral flux for better variance.
        "energy": _safe_number((rms_mean * 0.7) + (spectral_flux_mean * 0.3)),
        "danceability": min(1.0, max(0.0, danceability)),
    }


def normalize_features(db: Session, analysis_version: str) -> None:
    rows = db.execute(select(TrackFeaturesRaw).where(TrackFeaturesRaw.analysis_version == analysis_version)).scalars().all()
    if not rows:
        return

    def norm(value: float, low: float, high: float) -> float:
        if high <= low:
            return 0.5
        return max(0.0, min(1.0, (value - low) / (high - low)))

    bpm_values = [r.features_json.get("bpm", 0.0) for r in rows]
    loudness_values = [r.features_json.get("loudness", 0.0) for r in rows]
    centroid_values = [r.features_json.get("spectral_centroid", 0.0) for r in rows]
    rolloff_values = [r.features_json.get("spectral_rolloff", 0.0) for r in rows]
    key_strength_values = [r.features_json.get("key_strength", 0.0) for r in rows]
    onset_values = [r.features_json.get("onset_rate", 0.0) for r in rows]
    energy_values = [r.features_json.get("energy", 0.0) for r in rows]
    flux_values = [r.features_json.get("spectral_flux", 0.0) for r in rows]
    dyn_values = [r.features_json.get("dynamic_complexity", 0.0) for r in rows]

    bpm_min, bpm_max = min(bpm_values), max(bpm_values)
    loud_min, loud_max = min(loudness_values), max(loudness_values)
    cen_min, cen_max = min(centroid_values), max(centroid_values)
    roll_min, roll_max = min(rolloff_values), max(rolloff_values)
    ks_min, ks_max = min(key_strength_values), max(key_strength_values)
    onset_min, onset_max = min(onset_values), max(onset_values)
    energy_min, energy_max = min(energy_values), max(energy_values)
    flux_min, flux_max = min(flux_values), max(flux_values)
    dyn_min, dyn_max = min(dyn_values), max(dyn_values)

    for row in rows:
        features = row.features_json
        data = {
            "bpm": norm(features.get("bpm", 0.0), bpm_min, bpm_max),
            "loudness": norm(features.get("loudness", 0.0), loud_min, loud_max),
            "energy": norm(features.get("energy", 0.0), energy_min, energy_max),
            "danceability": features.get("danceability", 0.0),
            "spectral_centroid": norm(features.get("spectral_centroid", 0.0), cen_min, cen_max),
            "spectral_rolloff": norm(features.get("spectral_rolloff", 0.0), roll_min, roll_max),
            "mfcc_mean": features.get("mfcc_mean", 0.0),
            "mfcc_var": features.get("mfcc_var", 0.0),
            "key": features.get("key"),
            "scale": features.get("scale"),
            "key_strength": norm(features.get("key_strength", 0.0), ks_min, ks_max),
            "onset_rate": norm(features.get("onset_rate", 0.0), onset_min, onset_max),
            "spectral_flux": norm(features.get("spectral_flux", 0.0), flux_min, flux_max),
            "dynamic_complexity": norm(features.get("dynamic_complexity", 0.0), dyn_min, dyn_max),
        }
        stmt = insert(TrackFeaturesNorm).values(track_id=row.track_id, analysis_version=analysis_version, **data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[TrackFeaturesNorm.track_id, TrackFeaturesNorm.analysis_version],
            set_=data,
        )
        db.execute(stmt)

    db.commit()


def run_track_analysis(db: Session, track_id, analysis_version: str) -> dict:
    track = db.execute(select(Track).where(Track.id == track_id)).scalar_one()
    features = extract_features(track.file_path)
    raw_stmt = insert(TrackFeaturesRaw).values(
        track_id=track_id,
        analysis_version=analysis_version,
        features_json=features,
    )
    raw_stmt = raw_stmt.on_conflict_do_update(
        index_elements=[TrackFeaturesRaw.track_id, TrackFeaturesRaw.analysis_version],
        set_={"features_json": features, "updated_at": func.now()},
    )
    db.execute(raw_stmt)
    db.commit()
    normalize_features(db, analysis_version)
    return features
