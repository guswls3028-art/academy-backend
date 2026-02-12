-- 마이그레이션 상태 확인 SQL
-- 데이터베이스에서 직접 실행하여 확인

-- 1. teachers 앱의 마이그레이션 상태 확인
SELECT * FROM django_migrations WHERE app = 'teachers' ORDER BY applied;

-- 2. teachers_teacher 테이블 존재 여부 확인
SELECT EXISTS (
    SELECT FROM information_schema.tables 
    WHERE table_schema = 'public' 
    AND table_name = 'teachers_teacher'
);

-- 3. 모든 teachers 관련 테이블 확인
SELECT tablename FROM pg_tables 
WHERE schemaname = 'public' 
AND tablename LIKE '%teacher%';
