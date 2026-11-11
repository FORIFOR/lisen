from app.scoring import make_discrimination, score_dictation, tokenize


def texts(s):
    return [t.text for t in tokenize(s)]


def test_perfect_match_ignores_case_and_punctuation():
    r = score_dictation("Hi, can I get a small coffee, please?", "hi can i get a small coffee please")
    assert r.accuracy == 1.0
    assert r.errors == {}


def test_contraction_equivalence():
    assert score_dictation("I'm sorry, but I can't come today.", "I am sorry but I cannot come today").accuracy < 1.0  # "cannot" is one word
    assert score_dictation("I'm sorry, but I can't come today.", "I am sorry but I can not come today").accuracy == 1.0
    assert score_dictation("Are you gonna be home?", "are you going to be home").accuracy == 1.0


def test_numbers_digits_and_words_equivalent():
    assert texts("That's 6 dollars and 50 cents.") == texts("that's six dollars and fifty cents")
    assert texts("Around 7:30, if possible.") == texts("around seven thirty if possible")
    assert texts("It's 1,200 a month") == texts("it's twelve hundred a month")
    assert texts("starting at 149 dollars") == texts("starting at one hundred and forty nine dollars")
    assert texts("it's 4 8 1 5 2") == texts("it's four eight one five two")
    assert texts("the second corner") == texts("the 2nd corner")


def test_missing_function_word_is_weak_form():
    r = score_dictation("I meet my sister for lunch.", "I meet my sister lunch")
    assert r.errors == {"weak_forms": 1}
    assert r.accuracy == round(5 / 6, 4)


def test_ending_error_detected():
    r = score_dictation("We waited in line for nearly an hour.", "We wait in line for nearly an hour")
    assert r.errors.get("endings") == 1


def test_number_error_detected():
    r = score_dictation("The high today is 18 degrees.", "the high today is 80 degrees")
    assert r.errors == {"numbers": 1}


def test_contraction_error_detected():
    r = score_dictation("I'll have a muffin.", "I have a muffin")
    assert r.errors == {"contractions": 1}


def test_typo_is_accepted_but_flagged():
    r = score_dictation("Here's your receipt.", "here's your reciept")
    assert r.accuracy == 1.0
    assert any(t["status"] == "typo" for t in r.tokens)


def test_linking_classification():
    r = score_dictation("Can I pay by card?", "Can pay by card")
    assert r.errors == {"linking": 1} or r.errors == {"weak_forms": 1}


def test_extra_words_reported():
    r = score_dictation("Thanks a lot.", "thanks a lot of")
    assert r.accuracy == 1.0
    assert r.extra == ["of"]


def test_empty_answer():
    r = score_dictation("Thanks a lot.", "")
    assert r.accuracy == 0.0
    assert all(t["status"] == "missed" for t in r.tokens)


def test_discrimination_contains_original():
    d = make_discrimination("I'll have a blueberry muffin too.", prefer="contractions")
    assert d is not None
    assert d["options"][d["answer"]] == "I'll have a blueberry muffin too."
    assert len(set(d["options"])) == 2


def test_discrimination_rejects_context_free_word_and_ending_swaps():
    # Replacing "to" with "the" made a visibly ungrammatical diagnostic choice.
    assert make_discrimination("OK. I was gonna cook pasta. Do you want me to save you some?") is None
    assert make_discrimination("She ordered the tickets yesterday.") is None
    d = make_discrimination("I'd seen the result.")
    assert d is None  # "I'll seen" cannot be offered as a listening contrast
    assert make_discrimination("My suitcase didn't come out on the belt.") is None
    assert make_discrimination("I'll be your guide today.") is None


def test_plural_ending_is_not_a_typo():
    r = score_dictation("Do you sell magazines too?", "do you sell magazine too")
    assert r.errors == {"endings": 1}
