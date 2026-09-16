"""Evora speaker API. Legacy transcription responses retain text/language/speaker."""

import secrets
import time

from flask import jsonify, request, render_template

from evora_speakers import RATE, SpeakerAnalyzer, SpeakerRegistry, StreamSession, partition_turns


def register_routes(app, models, registry, transcribe, decode, lock):
    sessions = {}
    settings_nonce = secrets.token_urlsafe(32)
    analyzer = SpeakerAnalyzer(models, registry, transcribe)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

    def local_settings_access():
        return (request.remote_addr in {"127.0.0.1", "::1"}
                and secrets.compare_digest(request.headers.get("X-Evora-Settings", ""), settings_nonce))

    def uploaded_audio(limit=30):
        upload = request.files.get("file") or request.files.get("audio")
        if not upload:
            raise ValueError("Provide an audio file")
        audio = decode(upload.stream, sampling_rate=RATE)
        if not len(audio) or len(audio) > limit * RATE:
            raise ValueError(f"Audio must contain between 0 and {limit} seconds")
        return audio

    def selection():
        raw = request.form.get("allowed_speakers")
        if raw is None:
            return None
        # An empty selection accepts nobody, never everybody.
        try:
            return {int(x.strip()) for x in raw.split(",") if x.strip()}
        except ValueError:
            raise ValueError("allowed_speakers must be comma-separated speaker IDs")
    def selected(result):
        ids = selection()
        if ids is None:
            return result
        segments = [s for s in result["segments"] if s["accepted"] and s["speaker"]["id"] in ids]
        result.update(segments=segments, text="".join(s["text"] for s in segments).strip(), speaker=None,
                      filtered=True)
        if "provisional_segments" in result:
            result["provisional_segments"] = []
        return result

    @app.get("/speaker-settings")
    def speaker_settings_page():
        if request.remote_addr not in {"127.0.0.1", "::1"}:
            return "Open speaker settings on the Evora computer using localhost.", 403
        return render_template("speaker_settings.html", nonce=settings_nonce)

    @app.route("/speaker-settings", methods=["POST"])
    def speaker_settings_save():
        if not local_settings_access():
            return jsonify(error="Open settings on the Evora computer to make changes."), 403
        if not lock.acquire(blocking=False):
            return jsonify(error="Evora is processing audio. Try again shortly."), 409
        try:
            if models.state == "loading":
                return jsonify(error="Wait for model preparation to finish."), 409
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                raise ValueError("Expected settings object")
            models.configure(data)
            sessions.clear()
            return jsonify(models.status())
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        finally:
            lock.release()

    @app.get("/speaker-status")
    def speaker_status():
        return jsonify(models.status())

    @app.route("/speakers", methods=["GET", "DELETE"])
    def speakers():
        if not lock.acquire(blocking=False):
            return jsonify(error="Evora is processing audio."), 409
        try:
            if request.method == "DELETE":
                registry.reset()
                sessions.clear()
            return jsonify(enabled=models.state == "ready", backend="community-1 + ecapa",
                           threshold=registry.threshold, margin=registry.margin,
                           speakers=registry.summary(), speaker_session=registry.session_id,
                           error=models.error)
        finally:
            lock.release()

    @app.post("/speakers/threshold")
    def threshold():
        if not lock.acquire(blocking=False):
            return jsonify(error="Evora is processing audio."), 409
        try:
            data = request.get_json(silent=True) or {}
            value = float(data.get("threshold", "nan"))
            if not 0.3 <= value <= 0.95:
                raise ValueError("threshold must be between 0.3 and 0.95")
            registry.threshold = value
            for session in sessions.values():
                session.analyzer.registry.threshold = value
            return jsonify(ok=True, threshold=value)
        except (ValueError, TypeError):
            return jsonify(error="threshold must be a number between 0.3 and 0.95"), 400
        finally:
            lock.release()

    @app.post("/speakers/enroll")
    def enroll():
        if not local_settings_access():
            return jsonify(error="Use speaker settings on the Evora computer."), 403
        if not lock.acquire(blocking=False):
            return jsonify(error="Evora is processing audio."), 409
        try:
            if models.state != "ready":
                return jsonify(error="Prepare the speaker model first."), 409
            name = request.form.get("name", "").strip()
            if not name or len(name) > 80:
                raise ValueError("Enter a speaker name of 1 to 80 characters")
            audio = uploaded_audio(20)
            spans = partition_turns(models.turns(audio), len(audio) / RATE)
            active = {s for _, _, labels in spans for s in labels}
            if len(active) != 1 or any(len(labels) > 1 for _, _, labels in spans):
                raise ValueError("Enrollment needs one person speaking alone, without overlapping voices.")
            import numpy as np
            clean = np.concatenate([audio[int(a * RATE):int(b * RATE)] for a, b, labels in spans if labels])
            ident = registry.enroll(clean, name)
            sessions.clear()
            return jsonify(id=ident, name=name, confirmed=True)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception:
            return jsonify(error="Voice enrollment failed. Check the speaker model status."), 503
        finally:
            lock.release()

    @app.delete("/speakers/<int:ident>")
    def forget(ident):
        if not local_settings_access():
            return jsonify(error="Use speaker settings on the Evora computer."), 403
        if not lock.acquire(blocking=False):
            return jsonify(error="Evora is processing audio."), 409
        try:
            registry.voices.pop(ident, None)
            registry.save()
            sessions.clear()
            return jsonify(ok=True)
        finally:
            lock.release()

    @app.post("/v1/audio/transcriptions")
    def transcription():
        if not lock.acquire(blocking=False):
            return jsonify(error="Evora is already processing audio. Try again shortly.", busy=True), 429
        try:
            started = time.perf_counter()
            selection()
            audio = uploaded_audio()
            language = request.form.get("language", "").strip() or None
            want_speaker = request.form.get("speaker", "1").lower() not in {"0", "false", "no"}
            if want_speaker:
                result = analyzer.analyze(audio, language)
            else:
                words, detected = transcribe(audio, language)
                result = analyzer.result([analyzer.segment(w["start"], w["end"], w["text"], None,
                                                           False, "provisional") for w in words], detected)
            result = selected(result)
            result.update(duration=round(len(audio) / RATE, 3), processing_seconds=round(time.perf_counter() - started, 3))
            return jsonify(result)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception as exc:
            print(f"Transcription failed ({type(exc).__name__})")
            return jsonify(error="Audio processing failed. Check Evora's model status and service log."), 503
        finally:
            lock.release()

    @app.post("/v1/audio/stream")
    def stream():
        if not lock.acquire(blocking=False):
            return jsonify(error="Evora is processing audio.", busy=True), 429
        try:
            started = time.perf_counter()
            selection()
            audio = uploaded_audio(4)
            sequence = int(request.form.get("sequence", "0"))
            now = time.monotonic()
            for key in list(sessions):
                if now - sessions[key].touched > 120 or sessions[key].closed:
                    del sessions[key]
            session_id = request.form.get("session_id", "")
            if session_id not in sessions:
                if session_id:
                    raise ValueError("Unknown or expired session; start again without session_id")
                if sequence != 0:
                    raise ValueError("A new stream must begin with sequence 0")
                if len(sessions) >= 4:
                    return jsonify(error="All stream slots are in use."), 429
                session_id = secrets.token_urlsafe(24)
                stream_registry = SpeakerRegistry(registry.embed, registry.path,
                                                  threshold=registry.threshold, margin=registry.margin)
                sessions[session_id] = StreamSession(SpeakerAnalyzer(models, stream_registry, transcribe))
            final = request.form.get("final", "0").lower() in {"1", "true"}
            result = sessions[session_id].push(audio, sequence,
                                               request.form.get("language") or None, final)
            result = selected(result)
            result.update(session_id=session_id, processing_seconds=round(time.perf_counter() - started, 3))
            return jsonify(result)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception as exc:
            print(f"Streaming failed ({type(exc).__name__})")
            return jsonify(error="Streaming processing failed."), 503
        finally:
            lock.release()
