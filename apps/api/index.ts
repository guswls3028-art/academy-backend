// src/app/api/index.ts
import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL,
  withCredentials: true, // 🔥 쿠키 인증 필수
});

// ✅ 최소 수정: 테넌트 헤더 자동 추가
api.interceptors.request.use((config) => {
  // 브라우저 환경에서 window.TENANT_CODE 존재 시 적용
  const tenantCode = (window as any).TENANT_CODE;
  if (tenantCode) {
    config.headers = {
      ...config.headers,
      "X-Tenant-Code": tenantCode,
    };
  }
  return config;
});

export default api;
