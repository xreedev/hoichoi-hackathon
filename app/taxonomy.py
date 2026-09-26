"""One-line definitions of every SensitiveTagEnum value. Used by M5 prompts and M8 Choice criteria."""

from app.schemas import SensitiveTagEnum as T

SENSITIVE_TAG_DEFINITIONS: dict[str, str] = {
    T.funeral: "a funeral, cremation, shraddha, last rites, mourning ceremony, or a body/bier being carried",
    T.death: "someone dies, a dead body is shown, or characters learn of or discuss a death",
    T.grief: "characters mourn, weep over, or are visibly devastated by a loss",
    T.illness_or_hospital: "a hospital, clinic, doctor, sickbed, serious illness, injury treatment or "
                           "medical emergency",
    T.violence_or_blood: "physical fighting, assault, weapons used or brandished, threats of violence, blood "
                         "or gore",
    T.accident: "a road or vehicle accident, crash, fall, fire, or someone injured by mishap",
    T.crime_or_police: "a crime (theft, murder, kidnapping, fraud), police, arrest, or a criminal "
                       "investigation",
    T.alcohol_or_drugs: "drinking alcohol, drunkenness, smoking, or drug use",
    T.religious_ritual: "a puja, prayer, worship at a temple/mosque/church, or any religious ceremony",
    T.sexual_or_intimate: "sexual content, nudity, or intimate physical romance (kissing, bedroom scenes)",
    T.child_in_distress: "a child who is crying, frightened, lost, hurt, abused, or in danger",
    T.none: "none of the above sensitive contexts is present",
}

SENSITIVE_TAGS: list[str] = [t.value for t in T]
assert set(SENSITIVE_TAG_DEFINITIONS) == set(SENSITIVE_TAGS)


def tag_legend() -> str:
    return "\n".join(f"- {t}: {d}" for t, d in SENSITIVE_TAG_DEFINITIONS.items())
