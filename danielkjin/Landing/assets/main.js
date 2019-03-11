$(window).scroll(function(){
    var opacity = 1 - ($(this).scrollTop() / 350);

    $('#header header').css({"opacity":opacity});
});