import unittest
from doctoral_monitor.core import extract_document

class PublishedMediaTests(unittest.TestCase):
    def test_official_pdf_player_is_an_attachment_wrapper_not_empty_body(self):
        body='<html><title>2027年博士招生章程</title><div class="wp_articlecontent"><div class="wp_pdf_player" pdfsrc="/files/charter.pdf"></div></div></html>'.encode()
        doc=extract_document(body,'https://graduate.example.edu.cn/notice.htm',{})
        self.assertEqual(doc['body_status'],'attachment_wrapper')
        self.assertEqual(doc['assets'][0]['url'],'https://graduate.example.edu.cn/files/charter.pdf')
    def test_sibling_annex_and_explicit_plaintext_file_url_are_preserved(self):
        body=('<html><title>2027年博士招生章程</title><div class="articleTitle02"><div class="article2 art">'+('博士报考条件、报名时间、申请材料须按简章提交。'*10)+'联系方式 https://graduate.example.edu.cn/contact.pdf </div><div class="annex"><a href="form.docx">附件申请表</a></div></div></html>').encode()
        doc=extract_document(body,'https://graduate.example.edu.cn/article.htm',{})
        self.assertEqual({a['url'] for a in doc['assets']},{'https://graduate.example.edu.cn/form.docx','https://graduate.example.edu.cn/contact.pdf'})

if __name__=='__main__':unittest.main()
