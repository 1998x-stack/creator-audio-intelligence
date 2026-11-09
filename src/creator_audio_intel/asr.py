"""Local multilingual Whisper small, via faster-whisper/CTranslate2."""
from pathlib import Path
from .models import Segment


def transcribe_audio(path: str | Path, *, device='cpu', compute_type='int8', model_name='small') -> tuple[list[Segment],str,str]:
    try:
        from faster_whisper import WhisperModel
        import faster_whisper
    except ImportError as exc:
        raise RuntimeError('Install package with: pip install -e ".[asr]"') from exc
    # Multilingual "small", not English-only "small.en". Model downloads at first use.
    model=WhisperModel(model_name,device=device,compute_type=compute_type)
    segments,meta=model.transcribe(str(path),language='zh',beam_size=5,vad_filter=True,
                                    word_timestamps=False,condition_on_previous_text=False)
    records=[Segment(i,int(s.start*1000),int(s.end*1000),s.text.strip())
             for i,s in enumerate(segments) if s.text.strip()]
    if not records:
        raise RuntimeError('Whisper returned no speech segments')
    return records,meta.language,getattr(faster_whisper,'__version__','unknown')
