"""Identity pins of the two original synthetic learners in the Dawon preview."""
import hashlib


ORIGINAL_IDENTITIES = {
    1: ("6604c97cd54e3ce393dedda67aba17fa63b3339c331fa8471991fa1837c20813",
        "9c5812f6a6f099a6d655ba0eebd4ae0c372e17f1854ee80c969909d2b2310513",
        "b59461e58ee26f1d4149959479a45170a044ad6e896f0f5a2a09930f31dc32b2"),
    2: ("5d9968811f26d6313be476b66c94fb63488dcb4adf26c502fab3f84ca0f7ee52",
        "b59af466771e95a66dad4806f0cd57dbb4c0cb7b1d6974097fb6efe8bd19d527",
        "27d73a4319822823f694c80b47c93a3e1b03082684a3344c86c51d3506d21ae4"),
}


def matches_original(student):
    expected = ORIGINAL_IDENTITIES.get(student.pk)
    if student.tenant_id != 719 or not expected or not student.user_id or student.user.tenant_id != 719:
        return False
    return tuple(hashlib.sha256((value or "").encode()).hexdigest() for value in
                 (student.user.username, student.ps_number, student.omr_code)) == expected
