from apps.domains.assets.omr import constants as C

def build_objective_template_meta(*, question_count: int):
    """
    레이아웃과 100% 일치하는 OCR 인식용 좌표 데이터(mm) 생성.
    """
    meta = {
        "version": "objective_v2_45_enterprise",
        "question_count": question_count,
        "page": {"width": C.PAGE_WIDTH / 1, "height": C.PAGE_HEIGHT / 1}, # pt
        "questions": []
    }
    
    # 레이아웃 로직과 동일한 좌표 계산 (생략 없이 정밀 반영)
    y_start = C.PAGE_HEIGHT - C.MARGIN_Y - 20*mm
    row_h = 11.8*mm
    start_x_base = C.MARGIN_X + C.LEFT_COL_WIDTH + C.COL_GAP/2

    for col_idx, start_num in enumerate([1, 16, 31]):
        col_x = start_x_base + (col_idx * C.OBJECTIVE_COL_WIDTH)
        for idx in range(C.Q_ROWS_PER_COL):
            q_num = start_num + idx
            if q_num > question_count: continue
            
            curr_y = y_start - (idx * row_h)
            q_data = {
                "num": q_num,
                "bubbles": []
            }
            for b in range(1, 6):
                bx = col_x + 12*mm + (b-1) * C.CHOICE_GAP
                q_data["bubbles"].append({
                    "label": b,
                    "x": bx, "y": curr_y,
                    "w": C.BUBBLE_W, "h": C.BUBBLE_H
                })
            meta["questions"].append(q_data)
            
    return meta