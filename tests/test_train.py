"""Tests tokenizer training, saving-loading and encoding-decoding."""

from __future__ import annotations

from copy import deepcopy
from time import time
from typing import TYPE_CHECKING

import pytest
from tqdm import tqdm

import miditok
from miditok.constants import DEFAULT_TOKENIZER_FILE_NAME

from .utils_tests import (
    MIDI_PATHS_ONE_TRACK,
    TOKENIZATIONS_TRAIN,
    TOKENIZER_CONFIG_KWARGS,
    TRAINING_MODELS,
    adjust_tok_params_for_tests,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from typing import Any, Literal

VOCAB_SIZE = 2000
NUM_ADDITIONAL_TOKENS_SECOND_TRAINING = 400
default_params = deepcopy(TOKENIZER_CONFIG_KWARGS)
default_params.update(
    {
        "use_rests": True,
        "use_tempos": True,
        "use_time_signatures": True,
        "base_tokenizer": "TSD",
    }
)

TOK_PARAMS_TRAINING = []
for tokenization_ in TOKENIZATIONS_TRAIN:
    params_ = deepcopy(default_params)
    adjust_tok_params_for_tests(tokenization_, params_)
    TOK_PARAMS_TRAINING.append((tokenization_, params_))


@pytest.mark.parametrize("tok_params_set", TOK_PARAMS_TRAINING)
@pytest.mark.parametrize("model", TRAINING_MODELS)
def test_bpe_conversion(
    tok_params_set: tuple[str, dict[str, Any]],
    tmp_path: Path,
    model: Literal["BPE", "Unigram"],
    files_paths: Sequence[Path] = MIDI_PATHS_ONE_TRACK,
    vocab_size: int = VOCAB_SIZE,
):
    r"""
    Train a tokenizer, check encoding-decoding keeps the same data.

    It also tests tokenizer saving-loading, and resuming training.

    :param tok_params_set: tokenizer and its parameters to run.
    :param files_paths: list of paths of MIDI files to use for the tests.
    """
    # Creates tokenizers
    tokenization, params = tok_params_set
    tokenizer1: miditok.MIDITokenizer = getattr(miditok, tokenization)(
        tokenizer_config=miditok.TokenizerConfig(**params_)
    )
    tokenizer2: miditok.MIDITokenizer = getattr(miditok, tokenization)(
        tokenizer_config=miditok.TokenizerConfig(**params_)
    )

    # Trains them
    tokenizer1.train(
        vocab_size=vocab_size + NUM_ADDITIONAL_TOKENS_SECOND_TRAINING,
        model=model,
        files_paths=files_paths,
    )
    tokenizer2.train(vocab_size=vocab_size, model=model, files_paths=files_paths)
    tokenizer2.save_params(tmp_path)
    tokenizer2 = getattr(miditok, tokenization)(
        params=tmp_path / DEFAULT_TOKENIZER_FILE_NAME
    )
    tokenizer2.train(
        vocab_size=vocab_size + NUM_ADDITIONAL_TOKENS_SECOND_TRAINING,
        files_paths=files_paths,
    )

    # Tests _vocab_base and _vocab_base_id_to_byte are synced
    test_id_to_token = {
        id_: tokenizer2._vocab_base_byte_to_token[byte_]
        for id_, byte_ in tokenizer2._vocab_base_id_to_byte.items()
    }
    vocab_inv = {v: k for k, v in tokenizer2._vocab_base.items()}
    assert (
        test_id_to_token == vocab_inv
    ), "Vocabulary inversion failed, something is wrong with the way they are built"
    # Test the two tokenizers are identical
    assert len(tokenizer2) == vocab_size + NUM_ADDITIONAL_TOKENS_SECOND_TRAINING
    # Vocabs may have swapped ids orders for Unigram as the training is not 100%
    # deterministic.
    if model == "BPE":
        assert (
            tokenizer2 == tokenizer1
        ), "tokenizer 1-shot not equal to tokenizer 2-shots"

    # Checks tokens <--> ids <--> bytes conversions with one test case
    tokens = tokenizer1(files_paths[0], apply_bpe=False)
    if not tokenizer1.one_token_stream:
        tokens = tokens[0]
    tokenizer1.complete_sequence(tokens, complete_bytes=True)  # not done by default
    toks_from_bytes = tokenizer1._bytes_to_tokens(tokens.bytes)
    ids_from_toks = tokenizer1._tokens_to_ids(toks_from_bytes)
    bytes_from_ids = tokenizer1._ids_to_bytes(ids_from_toks, as_one_str=True)
    assert all(
        [
            bytes_from_ids == tokens.bytes,
            toks_from_bytes == tokens.tokens,
            ids_from_toks == tokens.ids,
        ]
    ), (
        "Conversion between tokens / bytes / ids failed, something is wrong in"
        "vocabularies"
    )

    # Unbatched encoding-decoding
    func_check = _check_bpe if model == "BPE" else _check_unigram
    at_least_one_error = False
    tok_time = 0
    samples_og = []  # used for batched
    for file_path in tqdm(files_paths, desc="Testing BPE unbatched"):
        # Tokenize file without BPE first
        tokens_original = tokenizer1(file_path, apply_bpe=False)
        if not tokenizer1.one_token_stream:
            tokens_original = tokens_original[0]
        samples_og.append(tokens_original)

        # Encode the tokens
        tokens1_encoded = deepcopy(tokens_original)
        tokens2_encoded = deepcopy(tokens_original)
        t0 = time()
        tokenizer1.apply_bpe(tokens1_encoded)
        tok_time += time() - t0
        tokenizer2.apply_bpe(tokens2_encoded)

        # Decode the tokens
        tokens1_decoded = deepcopy(tokens1_encoded)
        tokens2_decoded = deepcopy(tokens2_encoded)
        tokenizer1.decode_bpe(tokens1_decoded)
        tokenizer2.decode_bpe(tokens2_decoded)

        # Check everything went good
        at_least_one_error = (
            not func_check(
                tokens_original,
                tokens1_encoded,
                tokens2_encoded,
                tokens1_decoded,
                tokens2_decoded,
                tokenization,
                file_path.name,
            )
            or at_least_one_error
        )
    print(
        f"BPE encoding time un-batched: {tok_time:.2f} (mean:"
        f"{tok_time / len(files_paths):.4f})"
    )
    assert not at_least_one_error

    # Batched encoding-decoding
    samples1_encoded = deepcopy(samples_og)
    samples2_encoded = deepcopy(samples_og)
    t0 = time()
    tokenizer1.apply_bpe(samples1_encoded)
    tok_time = time() - t0
    tokenizer2.apply_bpe(samples2_encoded)

    samples1_decoded = deepcopy(samples1_encoded)
    samples2_decoded = deepcopy(samples2_encoded)
    tokenizer1.decode_bpe(samples1_decoded)
    tokenizer2.decode_bpe(samples2_decoded)

    for seq_og, seq1_enc, seq2_enc, seq1_dec, seq2_dec, file_path in zip(
        samples_og,
        samples1_encoded,
        samples2_encoded,
        samples1_decoded,
        samples2_decoded,
        files_paths,
    ):
        at_least_one_error = (
            not func_check(
                seq_og,
                seq1_enc,
                seq2_enc,
                seq1_dec,
                seq2_dec,
                tokenization,
                file_path.name,
            )
            or at_least_one_error
        )

    print(
        f"BPE encoding time batched: {tok_time:.2f} (mean:"
        f"{tok_time / len(files_paths):.4f})"
    )
    assert not at_least_one_error


def _check_bpe(
    seq_og: miditok.TokSequence,
    seq1_encoded: miditok.TokSequence,
    seq2_encoded: miditok.TokSequence,
    seq1_decoded: miditok.TokSequence,
    seq2_decoded: miditok.TokSequence,
    tokenization: str,
    file_name: str,
) -> bool:
    no_error = True
    if seq1_encoded.ids != seq2_encoded.ids:
        print(
            f"Encoding error for {tokenization} and {file_name}: "
            f"Encoded tokens not equal between two trained tokenizers"
        )
        no_error = False
    if not seq1_decoded.ids == seq2_decoded.ids == seq_og.ids:
        print(
            f"Decoding error for {tokenization}: "
            f"Decoded tokens do not match the original ones"
        )
        no_error = False
    return no_error


def _check_unigram(
    seq_og: miditok.TokSequence,
    seq1_encoded: miditok.TokSequence,
    seq2_encoded: miditok.TokSequence,
    seq1_decoded: miditok.TokSequence,
    seq2_decoded: miditok.TokSequence,
    tokenization: str,
    file_name: str,
) -> bool:
    # For unigram we just check lengths

    no_error = True
    if len(seq1_encoded.ids) != len(seq2_encoded.ids):
        print(
            f"Encoding error for {tokenization} and {file_name}: "
            f"Encoded tokens not equal between two trained tokenizers"
        )
        no_error = False
    if not len(seq1_decoded.ids) == len(seq2_decoded.ids) == len(seq_og.ids):
        print(
            f"Decoding error for {tokenization}: "
            f"Decoded tokens do not match the original ones"
        )
        no_error = False
    return no_error
