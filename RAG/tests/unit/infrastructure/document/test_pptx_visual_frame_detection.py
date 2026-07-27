"""PPTX 차트와 SmartArt graphic frame 탐지 규칙을 검증한다."""

from xml.etree import ElementTree

from jipsa_rag.infrastructure.document.images.models import DocumentImageKind
from jipsa_rag.infrastructure.document.images.pptx import _find_visual_frames


def test_find_visual_frames_classifies_chart_and_smartart() -> None:
    """graphicData URI와 표준 관계 요소를 이용해 두 시각 객체를 구분한다."""

    xml = """
    <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
           xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
           xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
           xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram">
      <p:cSld><p:spTree>
        <p:graphicFrame>
          <p:xfrm><a:off x="0" y="0"/><a:ext cx="1000" cy="1000"/></p:xfrm>
          <a:graphic><a:graphicData uri="chart"><c:chart/></a:graphicData></a:graphic>
        </p:graphicFrame>
        <p:graphicFrame>
          <p:xfrm><a:off x="1000" y="1000"/><a:ext cx="2000" cy="1500"/></p:xfrm>
          <a:graphic>
            <a:graphicData uri="diagram"><dgm:relIds/></a:graphicData>
          </a:graphic>
        </p:graphicFrame>
      </p:spTree></p:cSld>
    </p:sld>
    """

    frames = _find_visual_frames(ElementTree.fromstring(xml))

    assert [frame.kind for frame in frames] == [
        DocumentImageKind.PPTX_CHART_RENDER,
        DocumentImageKind.PPTX_SMARTART_RENDER,
    ]
    assert frames[0].shape_path.endswith("/chart")
    assert frames[1].shape_path.endswith("/smartart")
