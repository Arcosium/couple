# couple 안드로이드 앱 (`mobile/`)

`couple.ai-ve.uk` (PWA)를 전체화면 WebView 로 감싸는 가벼운 네이티브 래퍼.
ArcAI.ve 의 무거운 스택(Compose/Hilt/Retrofit/음성/위젯) 없이 **단일 액티비티 + WebView** 로만 구성.

## 무엇을 더하나 (브라우저 대비)
- **세션 영속** — 90일 매직링크 쿠키를 디스크에 flush → 앱 재시작에도 로그인 유지.
- **사진 업로드/다운로드** — `<input type=file>` 다중 선택, `Content-Disposition` 다운로드를 `DownloadManager` 로.
- **시스템 알림** — 웹의 `window.CoupleNative.notify({title,body,tag})` 호출 → 콕찌르기·리마인더가
  앱이 꺼져 있어도(포그라운드 아니어도) 상태바 알림으로 뜸.
  - 웹 연동 지점: `static/js/app.js` 의 `notifyNative()` → `handleWS()` 의 `poke`/`reminder` 분기.
  - 브라우저에서는 자동으로 기존 Web Notification 으로 폴백(앱/웹 양쪽 무탈).
- **뒤로가기 2번 종료**, 외부 링크(카카오맵 길찾기 등)는 외부 앱으로 위임.

## 빌드 (aarch64 OCI 박스, QEMU amd64 도커)
`/home/opc/android-build` 의 공용 빌드 인프라를 재사용한다. `REPO` 로 이 저장소를 가리키고 `:app` 모듈 타겟 지정:

```bash
cd /home/opc/android-build
REPO=/home/opc/projects/couple ./build.sh :app:assembleDebug
# 산출물: /home/opc/android-build/out/app-debug.apk
```

- 최초 1회: QEMU binfmt 등록 + 도커 이미지 빌드(에뮬레이션이라 느림). 이후는 Gradle 캐시로 빨라짐.
- 폰 설치: APK 를 폰으로 전송 후 사이드로드(알 수 없는 앱 설치 허용).

## 백엔드 주소 변경
`app/build.gradle.kts` 의 `COUPLE_URL` (`https://couple.ai-ve.uk`) 만 고치고 재빌드.

## Toolchain (ArcAI.ve 빌드에서 검증된 버전 핀)
Gradle 8.10.2 · AGP 8.7.3 · Kotlin 2.1.0 · JDK 17 · compileSdk/build-tools 35 · minSdk 30
