#!/usr/bin/python3 python

"""Multitrack test file
"""

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple, Union

import pytest
from miditoolkit import MidiFile, Pedal

import miditok

from .utils import (
    ALL_TOKENIZATIONS,
    MIDI_PATHS_MULTITRACK,
    TEST_LOG_DIR,
    TOKENIZER_CONFIG_KWARGS,
    adjust_tok_params_for_tests,
    prepare_midi_for_tests,
    tokenize_and_check_equals,
)

default_params = deepcopy(TOKENIZER_CONFIG_KWARGS)
# tempo decode fails without Rests for MIDILike because beat_res range is too short
default_params.update(
    {
        "use_chords": True,
        "use_rests": True,
        "use_tempos": True,
        "use_time_signatures": True,
        "use_sustain_pedals": True,
        "use_pitch_bends": True,
        "use_programs": True,
        "sustain_pedal_duration": False,
        "one_token_stream_for_programs": True,
        "program_changes": False,
    }
)
TOK_PARAMS_MULTITRACK = []
tokenizations_non_one_stream = [
    "TSD",
    "REMI",
    "MIDILike",
    "Structured",
    "CPWord",
    "Octuple",
]
tokenizations_program_change = ["TSD", "REMI", "MIDILike"]
for tokenization_ in ALL_TOKENIZATIONS:
    params_ = deepcopy(default_params)
    adjust_tok_params_for_tests(tokenization_, params_)
    TOK_PARAMS_MULTITRACK.append((tokenization_, params_))

    if tokenization_ in tokenizations_non_one_stream:
        params_tmp = deepcopy(params_)
        params_tmp["one_token_stream_for_programs"] = False
        # Disable tempos for Octuple with one_token_stream_for_programs, as tempos are
        # carried by note tokens
        if tokenization_ == "Octuple":
            params_tmp["use_tempos"] = False
        TOK_PARAMS_MULTITRACK.append((tokenization_, params_tmp))
    if tokenization_ in tokenizations_program_change:
        params_tmp = deepcopy(params_)
        params_tmp["program_changes"] = True
        TOK_PARAMS_MULTITRACK.append((tokenization_, params_tmp))


@pytest.mark.parametrize("midi_path", MIDI_PATHS_MULTITRACK)
def test_multitrack_midi_to_tokens_to_midi(
    midi_path: Union[str, Path],
    tok_params_sets: Optional[Sequence[Tuple[str, Dict[str, Any]]]] = None,
    saving_erroneous_midis: bool = False,
):
    r"""Reads a MIDI file, converts it into tokens, convert it back to a MIDI object.
    The decoded MIDI should be identical to the original one after downsampling, and
    potentially notes deduplication. We only parametrize for midi files, as it would
    otherwise require to load them multiple times each.
    # TODO test parametrize tokenization / params_set

    :param midi_path: path to the MIDI file to test.
    :param tok_params_sets: sequence of tokenizer and its parameters to run.
    :param saving_erroneous_midis: will save MIDIs decoded with errors, to be used to
        debug.
    """
    if tok_params_sets is None:
        tok_params_sets = TOK_PARAMS_MULTITRACK
    at_least_one_error = False

    # Reads the MIDI and add pedal messages
    midi = MidiFile(Path(midi_path))
    for ti in range(max(3, len(midi.instruments))):
        midi.instruments[ti].pedals = [
            Pedal(start, start + 200) for start in [100, 600, 1800, 2200]
        ]

    for tok_i, (tokenization, params) in enumerate(tok_params_sets):
        tokenizer: miditok.MIDITokenizer = getattr(miditok, tokenization)(
            tokenizer_config=miditok.TokenizerConfig(**params)
        )

        # Process the MIDI
        # midi notes / tempos / time signature quantized with the line above
        midi_to_compare = prepare_midi_for_tests(midi, tokenizer=tokenizer)

        # MIDI -> Tokens -> MIDI
        decoded_midi, has_errors = tokenize_and_check_equals(
            midi_to_compare, tokenizer, tok_i, midi_path.stem
        )

        if has_errors:
            TEST_LOG_DIR.mkdir(exist_ok=True, parents=True)
            at_least_one_error = True
            if saving_erroneous_midis:
                decoded_midi.dump(TEST_LOG_DIR / f"{midi_path.stem}_{tokenization}.mid")
                midi_to_compare.dump(
                    TEST_LOG_DIR / f"{midi_path.stem}_{tokenization}_original.mid"
                )

    assert not at_least_one_error
