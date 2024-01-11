"""Structured tokenizer."""

from __future__ import annotations

from symusic import Note, Score, Track

from miditok.classes import Event, TokSequence
from miditok.constants import MIDI_INSTRUMENTS
from miditok.midi_tokenizer import MIDITokenizer


class Structured(MIDITokenizer):
    r"""
    Structured tokenizer, with a recurrent token type succession.

    Introduced with the `Piano Inpainting Application <https://arxiv.org/abs/2002.00212>`_,
    it is similar to :ref:`TSD` but is based on a consistent token type successions.
    Token types always follow the same pattern: *Pitch* -> *Velocity* -> *Duration* ->
    *TimeShift*. The latter is set to 0 for simultaneous notes. To keep this property,
    no additional token can be inserted in MidiTok's implementation, except *Program*
    that can optionally be added preceding `Pitch` tokens. If you specify
    ``use_programs`` as ``True`` in the config file, the tokenizer will add *Program*
    tokens before each *Pitch* tokens to specify its instrument, and will treat all
    tracks as a single stream of tokens.

    **Note:** as ``Structured`` uses *TimeShifts* events to move the time from note to
    note, it can be unsuited for tracks with pauses longer than the maximum *TimeShift*
    value. In such cases, the maximum *TimeShift* value will be used.
    """

    def _tweak_config_before_creating_voc(self) -> None:
        self.config.use_chords = False
        self.config.use_rests = False
        self.config.use_tempos = False
        self.config.use_time_signatures = False
        self.config.use_sustain_pedals = False
        self.config.use_pitch_bends = False
        self.config.use_pitch_intervals = False
        self.config.program_changes = False

    def _create_track_events(self, track: Track, _: None = None) -> list[Event]:
        r"""
        Extract the tokens/events from a track (``symusic.Track``).

        Concerned events are: *Pitch*, *Velocity*, *Duration*, *NoteOn*, *NoteOff* and
        optionally *Chord*, *Pedal* and *PitchBend*.
        **If the tokenizer is using pitch intervals, the notes must be sorted by time
        then pitch values. This is done in** ``preprocess_midi``.

        :param track: ``symusic.Track`` to extract events from.
        :param _: in place of ``ticks_per_beat``, unused here as Structured do not
            support time signatures, hence the ticks_per_beat value is always the same
            and equal to the MIDI's time division.
        :return: sequence of corresponding ``Event``s.
        """
        # Make sure the notes are sorted first by their onset (start) times, second by
        # pitch: notes.sort(key=lambda x: (x.start, x.pitch)) done in midi_to_tokens
        program = track.program if not track.is_drum else -1
        events = []

        # Creates the Note On, Note Off and Velocity events
        previous_tick = 0
        for note in track.notes:
            # In this case, we directly add TimeShift events here so we don't have to
            # call __add_time_note_events and avoid delay cause by event sorting
            if not self.one_token_stream:
                time_shift_ticks = note.start - previous_tick
                if time_shift_ticks != 0:
                    time_shift = self._tpb_ticks_to_tokens[self.time_division][
                        time_shift_ticks
                    ]
                else:
                    time_shift = "0.0.1"
                events.append(
                    Event(
                        type_="TimeShift",
                        time=note.start,
                        desc=f"{time_shift_ticks} ticks",
                        value=time_shift,
                    )
                )
            # Note On / Velocity / Duration
            if self.config.use_programs:
                events.append(
                    Event(
                        type_="Program", value=program, time=note.start, desc=note.end
                    )
                )
            events.append(
                Event(type_="Pitch", value=note.pitch, time=note.start, desc=note.end)
            )
            events.append(
                Event(
                    type_="Velocity",
                    value=note.velocity,
                    time=note.start,
                    desc=f"{note.velocity}",
                )
            )
            dur = self._tpb_ticks_to_tokens[self.time_division][note.duration]
            events.append(
                Event(
                    type_="Duration",
                    value=dur,
                    time=note.start,
                    desc=f"{note.duration} ticks",
                )
            )
            previous_tick = note.start

        return events

    def _add_time_events(self, events: list[Event], time_division: int) -> list[Event]:
        r"""
        Create the time events from a list of global and track events.

        Internal method intended to be implemented by child classes.
        The returned sequence is the final token sequence ready to be converted to ids
        to be fed to a model.

        :param events: sequence of global and track events to create tokens time from.
        :param time_division: time division in ticks per quarter of the MIDI being
            tokenized.
        :return: the same events, with time events inserted.
        """
        all_events = []
        token_type_to_check = "Program" if self.one_token_stream else "Pitch"

        # Add "TimeShift" tokens before each "Pitch" tokens
        previous_tick = 0
        for event in events:
            if event.type_ == token_type_to_check:
                # Time shift
                time_shift_ticks = event.time - previous_tick
                if time_shift_ticks != 0:
                    time_shift = self._tpb_ticks_to_tokens[time_division][
                        time_shift_ticks
                    ]
                else:
                    time_shift = "0.0.1"
                all_events.append(
                    Event(
                        type_="TimeShift",
                        time=event.time,
                        desc=f"{time_shift_ticks} ticks",
                        value=time_shift,
                    )
                )
                previous_tick = event.time

            all_events.append(event)

        return all_events

    def _midi_to_tokens(self, midi: Score) -> TokSequence | list[TokSequence]:
        r"""
        Convert a **preprocessed** MIDI object to a sequence of tokens.

        We override the parent method to handle the "non-program" case where
        *TimeShift* events have already been added by `_notes_to_events`.
        The workflow of this method is as follows: the global events (*Tempo*,
        *TimeSignature*...) and track events (*Pitch*, *Velocity*, *Pedal*...) are
        gathered into a list, then the time events are added. If `one_token_stream` is
        ``True``, all events of all tracks are treated all at once, otherwise the
        events of each track are treated independently.

        :param midi: the MIDI :class:`symusic.Score` object to convert.
        :return: a :class:`miditok.TokSequence` if ``tokenizer.one_token_stream`` is
            ``True``, else a list of :class:`miditok.TokSequence` objects.
        """
        # Convert each track to tokens
        all_events = []

        # Adds note tokens
        if not self.one_token_stream and len(midi.tracks) == 0:
            all_events.append([])
        for track in midi.tracks:
            note_events = self._create_track_events(track)
            if self.one_token_stream:
                all_events += note_events
            else:
                all_events.append(note_events)

        # Add time events
        if self.one_token_stream:
            if len(midi.tracks) > 1:
                all_events.sort(key=lambda x: x.time)
            all_events = self._add_time_events(all_events)
            tok_sequence = TokSequence(events=all_events)
            self.complete_sequence(tok_sequence)
        else:
            tok_sequence = []
            for i in range(len(all_events)):
                # No call to __add_time_note_events here as not needed
                tok_sequence.append(TokSequence(events=all_events[i]))
                self.complete_sequence(tok_sequence[-1])

        return tok_sequence

    def _tokens_to_midi(
        self,
        tokens: TokSequence | list[TokSequence],
        programs: list[tuple[int, bool]] | None = None,
    ) -> Score:
        r"""
        Convert tokens (:class:`miditok.TokSequence`) into a MIDI.

        This is an internal method called by ``self.tokens_to_midi``, intended to be
        implemented by classes inheriting :class:`miditok.MidiTokenizer`.

        :param tokens: tokens to convert. Can be either a list of
            :class:`miditok.TokSequence` or a list of :class:`miditok.TokSequence`s.
        :param programs: programs of the tracks. If none is given, will default to
            piano, program 0. (default: ``None``)
        :return: the midi object (:class:`symusic.Score`).
        """
        # Unsqueeze tokens in case of one_token_stream
        if self.one_token_stream:  # ie single token seq
            tokens = [tokens]
        for i in range(len(tokens)):
            tokens[i] = tokens[i].tokens
        midi = Score(self.time_division)

        # RESULTS
        instruments: dict[int, Track] = {}

        def check_inst(prog: int) -> None:
            if prog not in instruments:
                instruments[prog] = Track(
                    program=0 if prog == -1 else prog,
                    is_drum=prog == -1,
                    name="Drums" if prog == -1 else MIDI_INSTRUMENTS[prog]["name"],
                )

        current_tick = 0
        current_program = 0
        current_instrument = None
        for si, seq in enumerate(tokens):
            # Set track / sequence program if needed
            if not self.one_token_stream:
                current_tick = 0
                is_drum = False
                if programs is not None:
                    current_program, is_drum = programs[si]
                current_instrument = Track(
                    program=current_program,
                    is_drum=is_drum,
                    name="Drums"
                    if current_program == -1
                    else MIDI_INSTRUMENTS[current_program]["name"],
                )

            # Decode tokens
            for ti, token in enumerate(seq):
                token_type, token_val = token.split("_")
                if token_type == "TimeShift" and token_val != "0.0.1":
                    current_tick += self._tpb_tokens_to_ticks[self.time_division][
                        token_val
                    ]
                elif token_type == "Pitch":
                    try:
                        if (
                            seq[ti + 1].split("_")[0] == "Velocity"
                            and seq[ti + 2].split("_")[0] == "Duration"
                        ):
                            pitch = int(seq[ti].split("_")[1])
                            vel = int(seq[ti + 1].split("_")[1])
                            duration = self._tpb_tokens_to_ticks[self.time_division][
                                seq[ti + 2].split("_")[1]
                            ]
                            new_note = Note(current_tick, duration, pitch, vel)
                            if self.one_token_stream:
                                check_inst(current_program)
                                instruments[current_program].notes.append(new_note)
                            else:
                                current_instrument.notes.append(new_note)
                    except IndexError:
                        # A well constituted sequence should not raise an exception,
                        # however with generated sequences this can happen, or if the
                        # sequence isn't finished
                        pass
                elif token_type == "Program":
                    current_program = int(token_val)

            # Add current_inst to midi and handle notes still active
            if not self.one_token_stream:
                midi.tracks.append(current_instrument)

        # create MidiFile
        if self.one_token_stream:
            midi.tracks = list(instruments.values())

        return midi

    def _create_base_vocabulary(self) -> list[str]:
        r"""
        Create the vocabulary, as a list of string tokens.

        Each token is given as the form ``"Type_Value"``, with its type and value
        separated with an underscore. Example: ``Pitch_58``.
        The :class:`miditok.MIDITokenizer` main class will then create the "real"
        vocabulary as a dictionary. Special tokens have to be given when creating the
        tokenizer, and will be added to the vocabulary by
        :class:`miditok.MIDITokenizer`.

        :return: the vocabulary as a list of string.
        """
        vocab = []

        # PITCH
        vocab += [f"Pitch_{i}" for i in range(*self.config.pitch_range)]

        # VELOCITY
        vocab += [f"Velocity_{i}" for i in self.velocities]

        # DURATION
        vocab += [
            f'Duration_{".".join(map(str, duration))}' for duration in self.durations
        ]

        # TIME SHIFT (same as durations)
        vocab.append("TimeShift_0.0.1")  # for a time shift of 0
        vocab += [
            f'TimeShift_{".".join(map(str, duration))}' for duration in self.durations
        ]

        # PROGRAM
        if self.config.use_programs:
            vocab += [f"Program_{program}" for program in self.config.programs]

        return vocab

    def _create_token_types_graph(self) -> dict[str, list[str]]:
        r"""
        Return a graph/dictionary of the possible token types successions.

        :return: the token types transitions dictionary.
        """
        dic = {
            "Pitch": ["Velocity"],
            "Velocity": ["Duration"],
            "Duration": ["TimeShift"],
            "TimeShift": ["Pitch"],
        }
        if self.config.use_programs:
            dic["Program"] = ["Pitch"]
            dic["TimeShift"] = ["Program"]
        return dic
