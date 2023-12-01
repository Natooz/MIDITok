#!/usr/bin/python3 python

"""Test methods

"""

from pathlib import Path
from typing import Optional, Sequence, Union

from tensorflow import Tensor as tfTensor
from tensorflow import convert_to_tensor
from torch import (
    FloatTensor as ptFloatTensor,
)
from torch import (
    IntTensor as ptIntTensor,
)
from torch import (
    Tensor as ptTensor,
)

import miditok

from .utils import HERE, MIDI_PATHS_ALL


def test_convert_tensors():
    original = [[2, 6, 95, 130, 25, 15]]
    types = [ptTensor, ptIntTensor, ptFloatTensor, tfTensor]

    tokenizer = miditok.TSD()
    for type_ in types:
        tensor = convert_to_tensor(original) if type_ == tfTensor else type_(original)
        tokenizer(tensor)  # to make sure it passes as decorator
        as_list = miditok.midi_tokenizer.convert_ids_tensors_to_list(tensor)
        assert as_list == original


def test_tokenize_datasets_file_tree(
    tmp_path: Path, midi_paths: Optional[Sequence[Union[str, Path]]] = None
):
    if midi_paths is None:
        midi_paths = MIDI_PATHS_ALL

    # Check the file tree is copied
    tokenizer = miditok.TSD(miditok.TokenizerConfig())
    tokenizer.tokenize_midi_dataset(midi_paths, tmp_path, overwrite_mode=True)
    json_paths = list(tmp_path.glob("**/*.json"))
    json_paths.sort(key=lambda x: x.stem)
    midi_paths.sort(key=lambda x: x.stem)
    for json_path, midi_path in zip(json_paths, midi_paths):
        assert json_path.relative_to(tmp_path).with_suffix(
            ".test"
        ) == midi_path.relative_to(HERE).with_suffix(".test"), (
            f"The file tree has not been reproduced as it should, for the file"
            f"{midi_path} tokenized {json_path}"
        )

    # Just make sure the non-overwrite mode doesn't crash
    tokenizer.tokenize_midi_dataset(midi_paths, tmp_path, overwrite_mode=False)
