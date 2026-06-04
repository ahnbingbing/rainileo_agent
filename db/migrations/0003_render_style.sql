-- 0003: Add render_style pipeline selector and output_video_path to cards table.
-- render_style tells the Cameraman Agent which pipeline to run.
-- output_video_path stores the final rendered mp4 path.

ALTER TABLE cards ADD COLUMN render_style TEXT
    CHECK (render_style IN ('ai_vtuber','cartoon_sticker','real_footage') OR render_style IS NULL);

ALTER TABLE cards ADD COLUMN output_video_path TEXT;
