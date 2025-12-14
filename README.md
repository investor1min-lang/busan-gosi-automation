# 부산 고시공고 자동 알림

부산시청 재개발/재건축 고시공고를 자동으로 감지하여 카카오톡으로 알림을 보내는 시스템입니다.

## 🚀 특징

- ✅ GitHub Actions로 클라우드에서 자동 실행
- ✅ 2시간마다 새 공고 확인
- ✅ 카카오톡 "나에게 보내기"로 즉시 알림
- ✅ 공고 이미지 자동 업로드 및 전송
- ✅ 중복 알림 방지

## 📋 사전 준비

### 1. 카카오 개발자 앱 설정

1. [Kakao Developers](https://developers.kakao.com/) 접속
2. 앱 생성 및 REST API 키 확인
3. "내 애플리케이션 > 앱 설정 > 플랫폼"에서 Web 플랫폼 추가
   - 사이트 도메인: `https://localhost:5000`
4. "카카오 로그인" 활성화
   - Redirect URI: `https://localhost:5000/oauth`
5. "동의항목"에서 "카카오톡 메시지 전송(talk_message)" 권한 설정

### 2. 카카오 토큰 발급

```bash
# 1. 인증 코드 받기 (브라우저에서 접속)
https://kauth.kakao.com/oauth/authorize?client_id={REST_API_KEY}&redirect_uri=https://localhost:5000/oauth&response_type=code&scope=talk_message

# 2. URL의 code 값 복사

# 3. 토큰 발급
curl -X POST "https://kauth.kakao.com/oauth/token" \
  -d "grant_type=authorization_code" \
  -d "client_id={REST_API_KEY}" \
  -d "redirect_uri=https://localhost:5000/oauth" \
  -d "code={위에서_복사한_CODE}"
```

### 3. imgbb API 키 발급

1. [imgbb API](https://api.imgbb.com/) 접속
2. "Get API Key" 클릭
3. 이메일로 가입 후 API 키 받기

## 🔧 설치 방법

### 1. GitHub 저장소 생성

1. GitHub에서 새 저장소 생성 (Public/Private 모두 가능)
2. 로컬에서 저장소 클론

```bash
git clone https://github.com/{사용자명}/{저장소명}.git
cd {저장소명}
```

### 2. 파일 업로드

다음 파일들을 저장소에 복사:
- `gosi_github_actions.py`
- `busan_blog_최종__1_.py`
- `requirements.txt`
- `gosi_state.json` (빈 파일: `{"processed": []}`)
- `.github/workflows/gosi.yml`

### 3. GitHub Secrets 설정

저장소 Settings > Secrets and variables > Actions > New repository secret

다음 4개의 Secret 추가:
- `KAKAO_REST_API_KEY`: 카카오 REST API 키
- `KAKAO_ACCESS_TOKEN`: 카카오 액세스 토큰
- `KAKAO_REFRESH_TOKEN`: 카카오 리프레시 토큰
- `IMGBB_API_KEY`: imgbb API 키

### 4. GitHub Actions 활성화

1. 저장소 Settings > Actions > General
2. "Allow all actions and reusable workflows" 선택
3. "Workflow permissions"에서 "Read and write permissions" 선택

### 5. 파일 푸시

```bash
git add .
git commit -m "Initial commit"
git push origin main
```

## ⏰ 실행 시간

- 한국시간 기준: 오전 7시, 9시, 11시, 오후 1시, 3시, 5시, 7시, 9시, 11시
- 2시간마다 자동 실행

## 🧪 수동 테스트

1. GitHub 저장소 > Actions 탭
2. "부산 고시공고 자동 알림" 워크플로우 선택
3. "Run workflow" 버튼 클릭

## 📱 알림 확인

- 카카오톡 "나와의 채팅"으로 메시지 수신
- 제목, 위치, 날짜, 이미지 링크 포함
- 이미지 URL 클릭 시 크게 볼 수 있음

## 🔍 로그 확인

1. Actions 탭에서 워크플로우 실행 내역 클릭
2. "고시공고 확인 및 알림" 단계 확인

## ⚠️ 주의사항

- 카카오 액세스 토큰은 5시간마다 만료됨 (자동 갱신)
- GitHub Actions 무료 사용량: 월 2000분 (Public 저장소는 무제한)
- imgbb 무료 업로드 제한: 확인 필요

## 📝 라이선스

MIT License

## 💡 문의

이슈나 개선사항은 GitHub Issues로 제보해주세요.
