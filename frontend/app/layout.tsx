import "./globals.css";
import Link from "next/link";
export const metadata = {
  title: "Jev · 가상거래 연구실",
  description: "100만 원 가상 자금으로 검증하는 시장 판단 연구 플랫폼",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <aside>
          <Link className="brand" href="/">
            jev<span> / 연구실</span>
          </Link>
          <div className="workspace">
            가상거래 연구 플랫폼<span>원화 기준 · 읽기 전용</span>
          </div>
          <nav>
            <Link href="/">현황</Link>
            <Link href="/decisions">판단 기록</Link>
            <Link href="/calibration">확률 검증</Link>
            <Link href="/evaluation">성과 평가</Link>
          </nav>
          <div className="aside-bottom">
            <i /> 가상 자금만 사용
            <br />
            <small>실제 금융 주문 없음</small>
          </div>
        </aside>
        <main>{children}</main>
      </body>
    </html>
  );
}
