"""Telling a figure from page furniture, and a caption from a file name (task B-156).

The cases are real: each was a figure stored from a crawled page. Measured on a sample of
40,000 stored figures, the furniture rules removed about half, and a random sample of what
they removed held logos, icons, social links, controls, seals and banners only.
"""

from __future__ import annotations

import pytest

from meridian_core.figures import is_filename_like, is_furniture, reader_caption


@pytest.mark.parametrize(
    ("image_url", "caption", "alt"),
    [
        ("https://x.test/static/images/logo-springer-nature-link-05805fde18.svg", None, "Springer"),
        ("https://x.test/assets/img/social/youtube-white.png", None, "YouTube"),
        ("https://x.test/default-album/footer-logo/ncis.png?sfvrsn=4606", None, "Cancer Institute"),
        ("https://x.test/images/icon-close.png", None, "close modal icon"),
        ("https://x.test/files/2022-01/3_1.png", None, "instagram"),
        ("https://x.test/images/orcid_16x16.png", None, "ORCID logo"),
        ("data:image/svg+xml;base64,PHN2Zz4=", None, "USDA Logo"),
        ("https://x.test/assets/subscribe-placeholder.png", None, "Subscribe Placeholder"),
    ],
)
def test_furniture_is_dropped(image_url: str, caption: str | None, alt: str | None) -> None:
    assert is_furniture(image_url, caption, alt)


@pytest.mark.parametrize(
    ("image_url", "caption", "alt"),
    [
        ("https://x.test/files/tpn0739-ai-safety-report-fig-3.3.png", None, "Pie chart"),
        ("https://x.test/uploads/2026/08/image009.jpg", None, "ENSO impact on rainfall anomalies"),
        # A photo sent through a messaging app is a photo; the app's name in its path is not chrome.
        (
            "https://x.test/uploads/WhatsApp-Image-2021-12-01-at-09.04.08.jpeg",
            None,
            "Delegates meet",
        ),
        # A long description that mentions a logo in passing is still a description.
        (
            "https://x.test/uploads/stage.jpg",
            None,
            "The minister speaks on stage in front of the agency logo at the new line opening",
        ),
        ("https://x.test/report.pdf", "Figure 3: Ridership by month, 2019–2024", None),
    ],
)
def test_figures_are_kept(image_url: str, caption: str | None, alt: str | None) -> None:
    assert not is_furniture(image_url, caption, alt)


@pytest.mark.parametrize(
    ("text", "image_url"),
    [
        (
            "olivia hutcherson 0 Wjcr3j8CU unsplash",
            "https://x.test/olivia-hutcherson-0Wjcr3j8CU-unsplash.jpg",
        ),
        ("BlueZonesWalkWeb 1", "https://x.test/uploads/BlueZonesWalkWeb-1.jpg"),
        ("blueprint health happiness 1", "https://x.test/uploads/blueprint_health_happiness_1.png"),
        # A thumbnail's size suffix is not part of the name.
        ("BlueZonesWalkWeb 1", "https://x.test/uploads/2018/09/BlueZonesWalkWeb-1-277x180.jpg"),
        (
            "blueprint health happiness 1",
            "https://x.test/uploads/2013/01/blueprint-health-happiness-1-277x180.jpg",
        ),
        ("IMG 2041", "https://x.test/photos/IMG_2041.JPG"),
        ("megamenu-3", "https://x.test/media/megamenu-3.png"),
        ("CriminalCourthouse", "https://x.test/img/criminalcourthouse.jpg"),
        (
            "adobestock_106294131_questions-answers-jointcommittee_jpeg_8",
            "https://x.test/files/s-answers-jointcommittee_jpeg_81252_0.jpg",
        ),
        ("Azhar Yusof-1", "https://x.test/hubfs/Azhar%20Yusof-1.jpg"),
        ("", "https://x.test/a.png"),
    ],
)
def test_a_file_name_is_not_a_caption(text: str, image_url: str) -> None:
    assert is_filename_like(text, image_url)


@pytest.mark.parametrize(
    ("text", "image_url"),
    [
        ("Ridership by month, 2019–2024", "https://x.test/uploads/fig3.png"),
        ("Pie chart", "https://x.test/tpn0739-ai-safety-report-fig-3.3.png"),
        ("Happiness metrics", "https://x.test/uploads/chart-7.png"),
        # The author named the file with the same plain words: still a description.
        ("happiness metrics", "https://x.test/uploads/2018/02/happiness-metrics-277x180.jpg"),
        ("literacy happiness", "https://x.test/uploads/2018/06/literacy-happiness-277x180.jpg"),
        # Found hidden by an earlier rule on the stored figures, and each is a description.
        ("Screenshot: Travel Monitoring", "https://x.test/images/travelmonitoring.jpg"),
        ("Chronological Table: Details3", "https://x.test/img/history03.gif"),
        ("Sage London circa 1971", "https://x.test/comms/sage-london-circa-1971.png"),
        ("SkillsFuture Credit", "https://x.test/accreditations/skillsfuture-credit.jpg"),
        ("Be Seen on TripZilla", "https://x.test/img/be_seen_on_tripzilla.webp"),
        ("tenders - EU4Health", "https://x.test/files/211130_Visuals-revamp_EU4Health_v012.png"),
        (
            "20260923 PM speaks at the 10th Anniversary",
            "https://x.test/up/20260923 PM speaks at the 10th Anniversary.JPG",
        ),
        ("Stills from animations created by Design102", "https://x.test/up/AnimationExamples.jpg"),
        (
            "Surabaya's former Mayor Tri Rismaharini © Toto Santiko Budi/ Shutterstock.com",
            "https://x.test/surabay-mayor.jpg",
        ),
        (
            "Central Bank 25th Anniversary Conference 2",
            "https://x.test/up/central-bank-25th-anniversary-conference-2.jpg",
        ),
    ],
)
def test_a_description_is_a_caption(text: str, image_url: str) -> None:
    assert not is_filename_like(text, image_url)


def test_the_reader_gets_the_first_label_that_describes() -> None:
    url = "https://x.test/uploads/olivia-hutcherson-0Wjcr3j8CU-unsplash.jpg"
    assert reader_caption("olivia hutcherson 0 Wjcr3j8CU unsplash", "Two people walking", url) == (
        "Two people walking"
    )
    assert reader_caption(None, "olivia hutcherson 0 Wjcr3j8CU unsplash", url) is None
    assert reader_caption("  Figure 2: Mode share  ", None, url) == "Figure 2: Mode share"
