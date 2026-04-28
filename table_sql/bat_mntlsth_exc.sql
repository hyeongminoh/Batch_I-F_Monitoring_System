-- ============================================================
-- BAT_MNTLST_EXC
-- 배치 모니터링 제외 파일 관리
-- ============================================================

CREATE TABLE BAT_MNTLST_EXC (
    MBRSH_PGM_ID    VARCHAR2(1)     NOT NULL,
    FILE_ID         VARCHAR2(10)    NOT NULL,
    EXCL_RSN        VARCHAR2(200),
    USE_YN          CHAR(1),
    REGR_ID         VARCHAR2(8),
    REG_DT          DATE,
    UPDR_ID         VARCHAR2(8),
    UPD_DT          DATE
);

COMMENT ON TABLE  BAT_MNTLST_EXC               IS '배치 모니터링 제외 파일 관리';
COMMENT ON COLUMN BAT_MNTLST_EXC.MBRSH_PGM_ID  IS '멤버쉽프로그램ID';
COMMENT ON COLUMN BAT_MNTLST_EXC.FILE_ID       IS '파일ID';
COMMENT ON COLUMN BAT_MNTLST_EXC.EXCL_RSN      IS '제외 사유';
COMMENT ON COLUMN BAT_MNTLST_EXC.USE_YN        IS '사용 여부';
COMMENT ON COLUMN BAT_MNTLST_EXC.REGR_ID       IS '등록자ID';
COMMENT ON COLUMN BAT_MNTLST_EXC.REG_DT        IS '등록일시';
COMMENT ON COLUMN BAT_MNTLST_EXC.UPDR_ID       IS '변경자ID';
COMMENT ON COLUMN BAT_MNTLST_EXC.UPD_DT        IS '변경일시';

-- 필요 시 PK/인덱스 예시 (테이블 설계에 맞게 조정)
-- ALTER TABLE BAT_MNTLST_EXC ADD CONSTRAINT PK_BAT_MNTLST_EXC PRIMARY KEY (MBRSH_PGM_ID, FILE_ID);
-- CREATE INDEX IDX_BAT_EXCL_USE ON BAT_MNTLST_EXC (USE_YN, FILE_ID);

-- 예시 데이터 (주석)
-- INSERT INTO BAT_MNTLST_EXC (MBRSH_PGM_ID, FILE_ID, EXCL_RSN, USE_YN, REGR_ID, REG_DT)
-- VALUES ('A', 'F0001', '비정기 수신 파일, 모니터링 불필요', 'Y', 'admin', SYSDATE);

COMMIT;
