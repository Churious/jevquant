import "./globals.css";
import Link from "next/link";
export const metadata = {
  title: "Jev 투자 대회 · 읽기 전용 가상거래",
  description: "6개 Jev 투자 방식과 3개 기준 전략의 독립 100만 원 가상거래",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <aside>
          <Link className="brand" href="/">
            jev<span> / 토너먼트</span>
          </Link>
          <div className="workspace">
            Jev 가상거래 대회<span>각 100만 원 · 읽기 전용</span>
          </div>
          <nav>
            <Link href="/">대회 순위</Link>
            <Link href="/results">대회 결과</Link>
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
