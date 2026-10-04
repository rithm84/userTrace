-- The agent cursor follows what the voice agent says: each clause it speaks is
-- judged for whether it clarifies or explains something on the participant's
-- screen, so that bundle and judgement carry their own purpose.
--
-- Contract: docs/arch-components/interview-and-voice.md
alter type public.context_purpose add value if not exists 'speech_gesture';
alter type public.attention_purpose add value if not exists 'speech_gesture';
