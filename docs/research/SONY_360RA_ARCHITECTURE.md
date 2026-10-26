# Sony 360 Reality Audio XM4 için nerede çalışıyor?

Tarih: 2026-10-26. Kaynaklar Sony'nin resmî ürün/geliştirici belgeleri; XM4 firmware ikilisi bu turda disassemble edilmedi.

## Bulgular

- [Sony'nin 360RA geliştirici sayfası](https://www.sony.co.jp/en/Products/360RA/licensing/) müzik servisleri için `Decoder/Renderer/Virtualizer` uygulamasını ve kulaklık eşlikçi uygulamasından kişiselleştirme parametresi almayı ayrı gereksinimler olarak veriyor. Aynı sayfa sertifikalı kulaklık tarafını kişiselleştirme desteği olarak tanımlıyor. Bu, WH-1000XM4 içinde sistemdeki her stereo kayda uygulanabilen gizli bir MPEG-H/360RA renderer olduğu iddiasını desteklemiyor.
- [Sony yaratıcı iş akışı](https://www.sony.co.jp/en/Products/create360RA/) 360RA içeriğinin çok kanallı stem/nesne verisinden oluşturulduğunu, salt stereo master'dan özgün nesne tabanlı master üretilemeyeceğini ve uyumlu akıllı telefon servis uygulaması üzerinden herhangi bir kulaklıkla dinlenebildiğini söylüyor. Spotify gibi sıradan stereo kayıttan gerçek üretici nesne konumları geri alınamaz.
- [WH-1000XM4 yardım kılavuzu](https://helpguide.sony.net/mdr/wh1000xm4/v1/en/contents/TP0002752840.html) A2DP müzik için SBC, AAC ve LDAC codec'lerini listeliyor. Bu tek başına iç DSP'de 3D yetenek olmadığını ispatlamaz; fakat akıllı telefondan kulaklığa ayrı 360RA nesne akışı gittiğine dair kanıt da sağlamaz.
- Sony, [WH-1000XM6 için](https://www.sony.com/en-bh/headphones/products/wh-1000xm6/developer-interview) stereo sesi kulaklık içinde gerçek zamanlı 360 Upmix for Cinema ile işlediğini açıkça anlatıyor. Bu başka bir ürün/özellik; XM4'e ikili firmware veya algoritma olarak port edilebilirliği gösterilmedi.

## Karar

XM4 üzerindeki mevcut ANC/EQ işlem yolunu ve 360RA servis tarafı renderer'ını ayrı tut. Şimdilik “sponsor kilidini açan firmware anahtarı” varsayımıyla flash deneyi planlama. En kısa kanıt yolu, uyumlu servis ve normal stereo uygulamasının kulaklığa gönderdiği codec/PCM çıktısını karşılaştırmak ve XM4 firmware'de MPEG-H decoder/renderer veya 360RA etkinleştirme komutu bulunduğuna dair somut tersine mühendislik bulgusunu aramaktır. Böyle bir bulgu çıkarsa mimari yeniden değerlendirilebilir; bulunmazsa mevcut açık PipeWire/Steam Audio yoluyla sistem sesini işlemek daha uygulanabilir.

Sony'nin 360RA uygulama dağıtımı format lisansı gerektiriyor. Açık MPEG-H standardı olması, servislerin korumalı içeriğine veya Sony'ye ait uygulama ikililerine erişim/yeniden dağıtım hakkı vermez. Bu bir lisans notudur; bağımsız stereo sanallaştırma veya açık HRTF kullanımı için Sony servis onayı gerektiği anlamına gelmez.
