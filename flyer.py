import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib import font_manager, rc

# ==================================================
# WINDOWS 한글 폰트 고정
# ==================================================
font_path = "C:/Windows/Fonts/malgun.ttf"
font_name = font_manager.FontProperties(fname=font_path).get_name()
rc("font", family=font_name)
plt.rcParams["axes.unicode_minus"] = False


def create_flyer():
    fig, ax = plt.subplots(figsize=(10, 18), dpi=300)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 18)
    ax.axis("off")

    # ==================================================
    # 컬러 시스템 (톤 다운 + 대비 강화)
    # ==================================================
    primary = "#0B3C5D"
    accent = "#1F7AE0"
    bg = "#FFFFFF"
    card_bg = "#F7F9FC"
    text_main = "#1C1C1C"
    text_sub = "#6B7280"
    divider = "#E5E7EB"

    # ==================================================
    # 헤더
    # ==================================================
    ax.text(
        5,
        17.4,
        "PARKCHEOL SCIENCE",
        ha="center",
        va="center",
        fontsize=11,
        color=text_sub,
    )

    ax.text(5, 16.6,
            "과학은 철두철미하게,\n결과는 철옹성처럼.",
            ha="center", va="center",
            fontsize=28, fontweight="bold", color=primary, linespacing=1.25)

    ax.text(5, 15.5,
            "언남 생명과학의 유일한 해답 · 박철 과학",
            ha="center", va="center",
            fontsize=15, color=text_sub)

    ax.plot([1.5, 8.5], [14.9, 14.9], color=divider, lw=1)

    # ==================================================
    # 카드 생성 함수
    # ==================================================
    def card(x, y, w, h):
        # 그림자
        ax.add_patch(
            patches.FancyBboxPatch(
                (x + 0.06, y - 0.06), w, h,
                boxstyle="round,pad=0.3",
                linewidth=0, facecolor="#000000", alpha=0.04
            )
        )
        # 카드
        ax.add_patch(
            patches.FancyBboxPatch(
                (x, y), w, h,
                boxstyle="round,pad=0.3",
                linewidth=0, facecolor=card_bg
            )
        )

    # ==================================================
    # POINT 01
    # ==================================================
    card(1, 9.6, 8, 4.6)

    ax.text(1.4, 13.9, "POINT 01", fontsize=11, color=accent, fontweight="bold")
    ax.text(1.4, 13.2,
            "언남고 최적화 프리미엄 커리큘럼",
            fontsize=20, fontweight="bold", color=text_main)

    items1 = [
        "언남고 기출 3개년 + 출제 트렌드 정밀 분석",
        "학교 부교재 연계 · 맞춤 변형 & 고난도 문항",
        "PC Lab 자체 제작 교재 + 실전 모의고사",
    ]

    y = 12.4
    for t in items1:
        ax.text(1.6, y, "▎", fontsize=18, color=accent, va="center")
        ax.text(1.8, y, t, fontsize=14.5, color=text_main)
        y -= 0.7

    # 시스템 이미지 영역
    ax.add_patch(
        patches.FancyBboxPatch(
            (2, 10.2), 6, 1.6,
            boxstyle="round,pad=0.2",
            linewidth=1, edgecolor=divider, facecolor="#FFFFFF"
        )
    )
    ax.text(5, 11.0,
            "PC Lab All-in-One Learning System",
            ha="center", va="center",
            fontsize=11, color=text_sub)

    # ==================================================
    # POINT 02
    # ==================================================
    card(1, 4.2, 8, 4.6)

    ax.text(1.4, 8.5, "POINT 02", fontsize=11, color=accent, fontweight="bold")
    ax.text(1.4, 7.8,
            "빈틈없는 학습 관리와 멘토링",
            fontsize=20, fontweight="bold", color=text_main)

    items2 = [
        "All-in-One 플랫폼으로 과제·영상·질의응답 통합",
        "실시간 미수행 과제 추적 · 즉각 개입",
        "무제한 고화질 복습 영상 제공",
        "1:1 멘토링 기반 개인별 학습 설계",
    ]

    y = 7.0
    for t in items2:
        ax.text(1.6, y, "▢", fontsize=13, color=accent, va="center")
        ax.text(1.9, y, t, fontsize=14.5, color=text_main)
        y -= 0.7

    # ==================================================
    # 푸터
    # ==================================================
    ax.plot([1.5, 8.5], [2.8, 2.8], color=divider, lw=1)

    ax.text(5, 2.1, "상담 문의", ha="center", fontsize=12, color=text_sub)
    ax.text(5, 1.5,
            "박철 과학연구소  010-3502-3313",
            ha="center", fontsize=18,
            fontweight="bold", color=text_main)

    plt.tight_layout()
    plt.savefig("ssot_flyer_premium.png", dpi=300, bbox_inches="tight")
    plt.close()


create_flyer()
